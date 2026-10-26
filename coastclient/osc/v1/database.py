# Copyright 2026 Gozunga and Coast contributors
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Database commands backed by the public engine catalog and API."""
import argparse
import ipaddress
import time
from urllib import parse

from keystoneauth1 import exceptions as ks_exc
from osc_lib import exceptions

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import command
from coastclient.osc.v1 import resource
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils


def database_api(app):
    api = app.client_manager.coast
    if api.max_microversion < (1, 29):
        raise exceptions.CommandError(
            'Database commands require Coast API 1.29 or later.')
    return api


def catalog(api):
    return utils.request(api, '/database_engines', 'GET')['database_engines']


def database_parser(parser, multiple=False, wait=False):
    parser.add_argument('database', metavar='DATABASE',
                        nargs='+' if multiple else None,
                        help='Database name or ID.')
    if wait:
        wait_parser(parser)
    return parser


def wait_parser(parser):
    parser.add_argument('--wait', action='store_true',
                        help='Wait for completion (up to 20 minutes).')


def wait_for(api, identity, target, deleted=False):
    deadline = time.monotonic() + 1200
    while True:
        try:
            response = api.request('/databases/' + identity, 'GET')
        except ks_exc.NotFound:
            if deleted:
                return
            raise
        row = utils.resource_data(utils.response_data(response), 'database')
        if not deleted and row.get('status') == target:
            return row
        if row.get('status') == 'ERROR':
            raise exceptions.CommandError(
                "Database '%s' entered ERROR while waiting." % identity)
        if time.monotonic() >= deadline:
            raise exceptions.CommandError(
                "Timed out waiting for database '%s'." % identity)
        time.sleep(2)


def public_port(value):
    port = utils.positive(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError('Use a port between 1024 and 65535.')
    return port


def source_cidr(value):
    try:
        ipaddress.IPv4Network(value, strict=False)
    except ValueError:
        raise argparse.ArgumentTypeError('Use an IPv4 CIDR.') from None
    return value


def public_parser(parser, disable=False):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--public', dest='public_enabled', action='store_true',
                       default=None, help='Enable public access.')
    if disable:
        group.add_argument('--no-public', dest='public_enabled',
                           action='store_false', help='Disable public access.')
    parser.add_argument('--public-port', type=public_port,
                        help='Published worker port (1024-65535); '
                             'omit to let Coast choose.')
    parser.add_argument('--allow-from', type=source_cidr,
                        help='Allowed source IPv4 CIDR; Coast defaults to '
                             '0.0.0.0/0 when enabling public access.')


def public_values(args):
    return {key: value for key, value in (
        ('enabled', args.public_enabled), ('port', args.public_port),
        ('source', args.allow_from)) if value is not None}


def show_database(app, data):
    row = dict(utils.resource_data(data, 'database'))
    if data.get('restart_required') or row.get('restart_required'):
        app.stderr.write('A database restart is required to apply changes.\n')
    public = row.get('public') or {}
    for key in ('port', 'host', 'private_host'):
        if key in public:
            row['public_' + key] = public[key]
    row['public'] = public.get('enabled', False)
    row['allowed_from'] = public.get('source')
    row.setdefault('public_url', None)
    return utils.show(row)


class ListDatabaseEngine(command.Lister):
    """List available database engines and versions from Coast."""

    column_priority = ()

    def take_action(self, args):
        fields = ('engine', 'display_name', 'versions', 'default_version',
                  'port', 'backups', 'has_database', 'has_username')
        return (('Engine', 'Name', 'Versions', 'Default', 'Port', 'Backups',
                 'Has database', 'Has username'),
                (utils.get_dict_properties(row, fields)
                 for row in catalog(database_api(self.app))))


class CreateDatabase(command.ShowOne):
    """Create a database using an engine and version from the catalog.

    Coast generates a strong password and stores it securely; reveal it with
    coast database credentials show NAME --show-password.
    """

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument('name', metavar='NAME',
                            help='Name shown in Coast; you type it to confirm '
                                 'a delete in the dashboard.')
        parser.add_argument('--engine', required=True,
                            help='Engine from coast database engine list.')
        parser.add_argument('--version',
                            help='Version from the engine catalog; '
                                 'omit to use its default.')
        for parent in ('appgroup', 'environment', 'server'):
            parser.add_argument('--' + parent, required=True,
                                help=('App group' if parent == 'appgroup'
                                      else parent.capitalize()) +
                                ' name or ID.')
        parser.add_argument(
            '--database',
            help='Initial database created inside the engine '
                 '(default: app). Only for engines that have databases; '
                 'see coast database engine list.')
        parser.add_argument(
            '--username',
            help='Database user for your app (default: app). Only for '
                 'engines that support named users; '
                 'see coast database engine list.')
        parser.add_argument(
            '--description',
            help='Free-text description shown in list and show output.')
        parser.add_argument('--cpu-limit', type=service.positive_cpu,
                            help='Positive CPU limit in cores.')
        parser.add_argument('--memory-limit-mb', type=utils.positive,
                            help='Positive memory limit in MiB.')
        public_parser(parser)
        wait_parser(parser)
        return parser

    def take_action(self, args):
        api = database_api(self.app)
        engine = next((row for row in catalog(api)
                       if row['engine'] == args.engine), None)
        if engine is None:
            raise exceptions.CommandError(
                'Choose an engine from coast database engine list.')
        if args.version is not None and args.version not in engine['versions']:
            raise exceptions.CommandError(
                'Choose a version from coast database engine list.')
        for field in ('database', 'username'):
            if getattr(args, field) is not None and not engine['has_' + field]:
                raise exceptions.CommandError(
                    'The selected engine does not accept --' + field + '.')
        values = {key: getattr(args, key) for key in (
            'name', 'engine', 'version', 'database', 'username', 'description',
            'cpu_limit', 'memory_limit_mb') if getattr(args, key) is not None}
        public = public_values(args)
        if public:
            values['public'] = public
        utils.resolve_references(api, args)
        values.update({key + '_id': getattr(args, key)
                       for key in ('appgroup', 'environment', 'server')})
        data = utils.request(
            api, '/databases', 'POST', json={'database': values})
        if args.wait:
            data = {'database': wait_for(
                api, utils.resource_data(data, 'database')['id'], 'ACTIVE')}
        self.app.stderr.write(
            'Coast generated a strong password. Reveal it with: '
            'coast database credentials show %s --show-password\n' %
            utils.resource_data(data, 'database')['name'])
        return show_database(self.app, data)


class ListDatabase(command.Lister):
    """List databases with optional placement and state filters."""

    admin_scope = 'list'

    def get_parser(self, prog_name):
        parser = utils.list_parser(super().get_parser(prog_name))
        for field, label in (('stack', 'stack'), ('appgroup', 'app group'),
                             ('environment', 'environment'),
                             ('server', 'server')):
            parser.add_argument(
                '--' + field,
                help='Only databases in this %s (name or ID).' % label)
        parser.add_argument(
            '--engine', help='Only databases of this engine '
                             '(see coast database engine list).')
        parser.add_argument(
            '--status', help='Only databases in this status, '
                             'for example ACTIVE or ERROR.')
        return parser

    def take_action(self, args):
        api = database_api(self.app)
        utils.resolve_references(api, args)
        filters = {key + '_id': getattr(args, key) for key in (
            'stack', 'appgroup', 'environment', 'server')
            if getattr(args, key) is not None}
        filters.update({key: getattr(args, key) for key in ('engine', 'status')
                        if getattr(args, key) is not None})
        rows = utils.list_data(api, '/databases', 'databases', args, filters)
        fields = ('id', 'name', 'engine', 'version', 'status',
                  'appgroup_id', 'environment_id', 'server_id')
        return utils.project_column(args, rows, (
            fields, (utils.get_dict_properties(row, fields) for row in rows)))


class ShowDatabase(command.ShowOne):
    """Show a database."""

    def get_parser(self, prog_name):
        return database_parser(super().get_parser(prog_name))

    def take_action(self, args):
        api = database_api(self.app)
        identity = utils.find_resource(api, args.database, 'database')
        return show_database(self.app, utils.request(
            api, '/databases/' + identity, 'GET'))


class DeleteDatabase(command.Command):
    """Delete one or more databases and optionally wait for removal."""

    def get_parser(self, prog_name):
        return database_parser(super().get_parser(prog_name), True, True)

    def take_action(self, args):
        api = database_api(self.app)
        for reference in args.database:
            utils.CONTEXT.get().args['database'] = reference
            identity = utils.find_resource(api, reference, 'database')
            utils.request(api, '/databases/' + identity, 'DELETE')
            if args.wait:
                wait_for(api, identity, None, deleted=True)


class StartDatabase(command.Command):
    """Start a stopped database."""

    action = 'start'
    target = 'ACTIVE'

    def get_parser(self, prog_name):
        return database_parser(super().get_parser(prog_name), wait=True)

    def take_action(self, args):
        api = database_api(self.app)
        identity = utils.find_resource(api, args.database, 'database')
        utils.request(api, '/databases/' + identity + '/action', 'POST',
                      json={self.action: {}})
        if args.wait:
            wait_for(api, identity, self.target)


class StopDatabase(StartDatabase):
    """Stop an active database."""

    action = 'stop'
    target = 'STOPPED'


class RestartDatabase(StartDatabase):
    """Restart an active database."""

    action = 'restart'


class LogsDatabase(command.Lister):
    """Read database runtime logs once."""

    def get_parser(self, prog_name):
        parser = database_parser(super().get_parser(prog_name))
        parser.add_argument('--tail', type=utils.nonnegative,
                            help='Recent line count; zero returns no lines.')
        parser.add_argument('--since', help='ISO 8601 UTC timestamp.')
        return parser

    def take_action(self, args):
        api = database_api(self.app)
        identity = utils.find_resource(api, args.database, 'database')
        params = {key: getattr(args, key) for key in ('tail', 'since')
                  if getattr(args, key) is not None}
        log = utils.request(api, '/databases/' + identity + '/logs',
                            'GET', params=params)['logs']
        utils.log_notices(self.app, log)
        return (('Timestamp', 'Stream', 'Line'),
                (utils.get_dict_properties(row, ('ts', 'stream', 'line'))
                 for row in log['lines']))


class ShowDatabaseCredentials(command.ShowOne):
    """Show secret database credentials; protect this output."""

    raw_field_names = True

    def get_parser(self, prog_name):
        parser = database_parser(super().get_parser(prog_name))
        parser.add_argument('--show-password', action='store_true',
                            help='Reveal secret passwords and credentials '
                                 'in URLs. Output is secret.')
        return parser

    def take_action(self, args):
        api = database_api(self.app)
        identity = utils.find_resource(api, args.database, 'database')
        row = dict(utils.resource_data(utils.request(
            api, '/databases/' + identity + '/credentials', 'GET'),
            'credentials'))
        if not args.show_password:
            for key in ('password', 'root_password'):
                if key in row:
                    row[key] = '********'
            for key in ('internal_url', 'public_url'):
                if row.get(key):
                    url = parse.urlsplit(row[key])
                    if '@' in url.netloc:
                        user, host = url.netloc.rsplit('@', 1)
                        if ':' in user:
                            row[key] = parse.urlunsplit(url._replace(
                                netloc=user.split(':', 1)[0] + ':********@' +
                                host))
        # Preserve API field names for scripts using -c password.
        return utils.show(row)


class SetDatabase(command.ShowOne):
    """Update a database's name, limits and public access."""

    route = '/databases/{database_id}'
    method = 'PATCH'

    def get_parser(self, prog_name):
        parser = database_parser(super().get_parser(prog_name))
        for field in ('name', 'description'):
            parser.add_argument('--' + field,
                                help=utils.label(field) + ' value.')
        parser.add_argument('--cpu-limit', type=service.positive_cpu,
                            help='Positive CPU limit in cores.')
        parser.add_argument('--memory-limit-mb', type=utils.positive,
                            help='Positive memory limit in MiB.')
        public_parser(parser, disable=True)
        return parser

    def values(self, args):
        values = {key: getattr(args, key) for key in (
            'name', 'description', 'cpu_limit', 'memory_limit_mb')
            if getattr(args, key) is not None}
        public = public_values(args)
        if public:
            values['public'] = public
        return values

    def take_action(self, args):
        api = database_api(self.app)
        values = self.values(args)
        if not values:
            raise exceptions.CommandError('Nothing to change.')
        identity = utils.find_resource(api, args.database, 'database')
        return show_database(self.app, utils.request(
            api, '/databases/' + identity, 'PATCH', json={'database': values}))


class UnsetDatabase(SetDatabase):
    """Remove database resource limits."""

    def get_parser(self, prog_name):
        parser = database_parser(command.ShowOne.get_parser(self, prog_name))
        for field in ('cpu-limit', 'memory-limit-mb'):
            parser.add_argument('--' + field, action='store_true',
                                help='Remove the ' + field + ' limit.')
        return parser

    def values(self, args):
        return {key: None for key in ('cpu_limit', 'memory_limit_mb')
                if getattr(args, key)}


class DatabaseBackup:
    """Resolve databases without using service routes or engine defaults."""

    def get_parser(self, prog_name):
        parser = database_parser(command.CoastCommand.get_parser(
            self, prog_name))
        for field in self.options:
            parser.add_argument('--' + field.replace('_', '-'),
                                type=utils.nonnegative,
                                help=resource.OPTION_HELP[field])
        if self.method == 'PUT':
            group = parser.add_mutually_exclusive_group()
            group.add_argument('--enable', dest='enabled', action='store_true',
                               default=None, help='Enable scheduled backups.')
            group.add_argument('--disable', dest='enabled',
                               action='store_false',
                               help='Disable scheduled backups.')
        return parser

    def take_action(self, args):
        api = database_api(self.app)
        identity = utils.find_resource(api, args.database, 'database')
        values = {key: getattr(args, key) for key in self.options
                  if getattr(args, key) is not None}
        if self.method == 'PUT':
            if args.enabled is not None:
                values['enabled'] = args.enabled
            if not values:
                raise exceptions.CommandError('Nothing to change.')
        kwargs = {}
        if self.method in ('POST', 'PUT'):
            kwargs['json'] = ({self.envelope: values}
                              if self.method == 'PUT' else values)
        data = utils.request(api, self.route.format(database_id=identity),
                             self.method, **kwargs)
        if isinstance(self, command.Lister):
            return self.fields, (utils.get_dict_properties(row, self.fields)
                                 for row in data[self.envelope])
        return utils.show(utils.resource_data(data, self.envelope))


class ShowDatabaseBackupPolicy(DatabaseBackup, catchup.BackupPolicyShow):
    """Show a database's backup schedule and retention policy."""

    route = '/databases/{database_id}/backup_policy'


class SetDatabaseBackupPolicy(DatabaseBackup, catchup.BackupPolicySet):
    """Set a database's backup schedule; Coast derives engine and container."""

    route = '/databases/{database_id}/backup_policy'
    options = ('hour', 'minute', 'keep_daily', 'keep_weekly')


class CreateDatabaseBackup(DatabaseBackup, catchup.BackupCreate):
    """Start a database backup without waiting for completion."""

    route = '/databases/{database_id}/backups'


class ListDatabaseBackup(DatabaseBackup, catchup.BackupList):
    """List backups for a database."""

    route = '/databases/{database_id}/backups'
