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

"""Service commands using the public Coast API."""
import argparse
import math
from pathlib import Path
import re
import sys
import time

from keystoneauth1 import exceptions as ks_exc
from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import env
from coastclient.osc.v1 import utils


SOURCE_HELP = {
    'repo-url': 'Git repository URL.',
    'branch': 'Git branch to build.',
    'dockerfile-path': 'Dockerfile path within the repository.',
    'compose-path': 'Compose file path within the repository.',
    'base-directory': 'Build base directory within the repository.',
    'image': 'Container image reference.',
    'tag': 'Container image tag.',
}


def source_options(parser):
    key = parser.add_mutually_exclusive_group()
    key.add_argument('--deploy-key', choices=('generate',),
                     help='Generate a deploy key on the server.')
    key.add_argument('--deploy-key-file', metavar='FILE|-',
                     help='Unencrypted private key file; - reads stdin.')
    parser.add_argument('--dockerfile', metavar='FILE|-',
                        help='UTF-8 Dockerfile; - reads stdin. Save without '
                             'deploying when used with app set.')


def read_source(path):
    try:
        return (sys.stdin.read() if path == '-' else
                Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError):
        raise exceptions.CommandError(
            "Unable to read file '%s'." % path) from None


def source_version(api, version):
    if api.max_microversion < tuple(map(int, version.split('.'))):
        raise exceptions.CommandError(
            'App source needs API version %s or later.' % version)
    return {'source_microversion': version}


def deploy_key(args):
    if args.deploy_key_file is not None:
        return {'private_key': read_source(args.deploy_key_file)}
    if args.deploy_key is not None:
        return {'generate': True}
    return None


def public_app(row):
    row = dict(row)
    key = (row.get('source') or {}).get('deploy_key') or {}
    for field in ('public_key', 'fingerprint'):
        if field in key:
            row['deploy_key_' + field] = key[field]
    return row


def key_hint(app, row, name):
    key = (row.get('source') or {}).get('deploy_key') or {}
    if key.get('public_key'):
        app.stderr.write(key['public_key'] + '\n')
        app.stderr.write('Add this deploy key to your repository, then run '
                         '`openstack coast deploy %s`.\n' % name)


def wait_for_deploy_key(api, path, old_key, **options):
    deadline = time.monotonic() + 30
    while True:
        row = utils.resource_data(
            utils.request(api, path, 'GET', **options), 'service')
        key = (row.get('source') or {}).get('deploy_key') or {}
        if key.get('public_key') and key['public_key'] != old_key:
            return row, True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Do not present the active key as the requested replacement.
            row = dict(row)
            row['source'] = dict(row.get('source') or {})
            row['source'].pop('deploy_key', None)
            return row, False
        time.sleep(min(2, remaining))


def health_check_parser(parser, update=False):
    parser.add_argument(
        '--health-check', choices=('none', 'http'),
        help='none: ACTIVE once the container runs stably (default for new '
             'apps); ports are optional. http: also wait until the app '
             'answers HTTP on its probe port.')
    port = parser.add_mutually_exclusive_group()
    port.add_argument('--health-check-port', type=health_check_port,
                      help='HTTP probe port (default: first app port). '
                           'Cannot be used with --health-check none.')
    if update:
        port.add_argument(
            '--no-health-check-port', action='store_true',
            help='Clear the stored HTTP probe port override so the first '
                 'app port is probed.')
    parser.add_argument('--health-check-path',
                        help='HTTP probe path (default: /). '
                             'Cannot be used with --health-check none.')


def health_check_port(value):
    try:
        number = int(value)
        if 1 <= number <= 65535:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use a port from 1 to 65535.')


def health_check_values(args, values):
    if (args.health_check == 'none' and
            (args.health_check_port is not None or
             args.health_check_path is not None)):
        raise exceptions.CommandError(
            '--health-check-port and --health-check-path cannot be used '
            'with --health-check none.')
    if args.health_check is not None:
        values['health_check'] = args.health_check
    if args.health_check_port is not None:
        values['health_check_port'] = args.health_check_port
    if getattr(args, 'no_health_check_port', False):
        values['health_check_port'] = None
    if args.health_check_path is not None:
        values['healthcheck_path'] = args.health_check_path
    if any(key in values for key in (
            'health_check', 'health_check_port', 'healthcheck_path')):
        return {'health_check_microversion': '1.4'}
    return {}


class _CreateWorkload(command.ShowOne):
    """Shared create for apps and services (Q148): one /v1/services row."""

    kinds = ()

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument('name', metavar='NAME',
                            help='Name for the new %s.' % (
                                'app' if 'git' in self.kinds else 'service'))
        for parent in ('appgroup', 'environment'):
            parser.add_argument('--' + parent, required=True,
                                metavar=parent.upper(),
                                help='%s name or ID.' % (
                                    'App group' if parent == 'appgroup'
                                    else parent.capitalize()))
        parser.add_argument('--server', required=True, metavar='SERVER',
                            help="Worker server name or ID in the app group's "
                                 'stack.')
        if 'git' in self.kinds:
            source_options(parser)
            parser.add_argument('--compose-file', metavar='FILE|-',
                                help='UTF-8 Compose file; - reads stdin.')
            for field in ('repo-url', 'branch', 'dockerfile-path',
                          'compose-path', 'base-directory', 'image', 'tag'):
                parser.add_argument('--' + field,
                                    help=SOURCE_HELP[field])
            parser.add_argument('--build-pack', choices=(
                'nixpacks', 'dockerfile', 'compose', 'static'),
                help='Build method: nixpacks, dockerfile, compose or '
                     'static.')
        else:
            parser.add_argument('--template', type=template_id,
                                metavar='TEMPLATE',
                                help='Template slug from '
                                     'openstack coast template list.')
            parser.add_argument('--image',
                                help='Container image (with --tag).')
            parser.add_argument('--tag',
                                help='Container image tag.')
        parser.allow_abbrev = False
        parser.add_argument('--description',
                            help='%s description.' % (
                                'App' if 'git' in self.kinds else 'Service'))
        parser.add_argument(
            '--ports', dest='ports',
            help='Comma-separated container ports (1-65535). Required only '
                 'when --health-check http or the app '
                 'has a domain.')
        health_check_parser(parser)
        parser.add_argument('--cpu-limit', type=positive_cpu,
                            help='CPU limit in cores (positive number; '
                                 'fractions accepted).')
        parser.add_argument('--memory-limit-mb', type=utils.positive,
                            help='Memory limit in MiB (positive integer).')
        parser.add_argument('--env-file', metavar='FILE|-',
                            help='Initial dotenv file or stdin (-), with '
                                 'secret, runtime-only and build-time '
                                 'markers.')
        parser.add_argument('--no-deploy', action='store_true',
                            help='Create the resource without starting a '
                                 'deployment.')
        return parser

    def source_type(self, args):
        return 'template'

    def take_action(self, args):
        fields = {
            'git': ('repo_url', 'branch', 'build_pack', 'dockerfile_path',
                    'compose_path', 'base_directory'),
            'image': ('image', 'tag'), 'template': ('template',),
            'dockerfile': ('dockerfile',), 'compose': ('compose_file',)}
        required = {'git': ('repo_url',), 'image': ('image', 'tag'),
                    'template': ('template',), 'dockerfile': (), 'compose': ()}
        source_type = self.source_type(args)
        allowed = fields[source_type]
        for kind in self.kinds:
            for key in fields[kind]:
                if getattr(args, key, None) is not None and key not in allowed:
                    raise exceptions.CommandError(
                        'Choose either --repo-url or --image/--tag.')
        for key in required[source_type]:
            if getattr(args, key) is None:
                message = ('--image requires --tag.' if key == 'tag' else
                           '--tag requires --image.')
                raise exceptions.CommandError(message)
        source = {'type': source_type}
        for key in allowed:
            value = getattr(args, key)
            if value is not None:
                source['template_id' if key == 'template' else key] = value
        source_options = {}
        if source_type in ('dockerfile', 'compose'):
            source_options = source_version(
                self.app.client_manager.coast, '1.31')
            source = {'type': source_type, source_type: read_source(
                args.dockerfile if source_type == 'dockerfile'
                else args.compose_file)}
        if 'git' in self.kinds and (args.deploy_key or
                                    args.deploy_key_file is not None):
            if source_type != 'git':
                raise exceptions.CommandError(
                    'Deploy keys require --repo-url.')
            source_options = source_version(
                self.app.client_manager.coast, '1.30')
            source['deploy_key'] = deploy_key(args)
        if (args.env_file is not None and tuple(map(
                int, self.app.client_manager.coast.microversion.split('.')))
                < (1, 0)):
            raise exceptions.CommandError(
                '--env-file requires Coast API 1.0 or later.')
        initial_env = (env.read_dotenv(args.env_file)
                       if args.env_file is not None else None)
        utils.resolve_references(self.app.client_manager.coast, args)
        values = {'name': args.name, 'appgroup_id': args.appgroup,
                  'environment_id': args.environment, 'server_id': args.server,
                  'source': source}
        for key in ('description', 'ports', 'cpu_limit', 'memory_limit_mb'):
            if getattr(args, key) is not None:
                values[key] = getattr(args, key)
        if initial_env is not None:
            values['env'] = initial_env
        probe_options = health_check_values(args, values)
        probe_options.update(source_options)
        if args.no_deploy or source.get('deploy_key', {}).get('generate'):
            values['deploy'] = False
        if 'git' in self.kinds:
            values['kind'] = ('service' if source_type == 'compose' else 'app')
            probe_options['kind_microversion'] = '1.11'
        elif source_type == 'image' and 'template' in self.kinds:
            # An image service stays a service (API 1.11 stores the kind).
            values['kind'] = 'service'
            probe_options['kind_microversion'] = '1.11'
        data = utils.request(self.app.client_manager.coast, '/services',
                             'POST',
                             json={'service': values}, **probe_options)
        row = utils.resource_data(data, 'service')
        if source.get('deploy_key', {}).get('generate'):
            key_hint(self.app, row, args.name)
        return utils.show(public_app(row))


class CreateApp(_CreateWorkload):
    """Create a Git, image, Dockerfile or Compose workload."""

    kinds = ('git', 'image', 'dockerfile', 'compose')

    def source_type(self, args):
        selected = sum(value is not None for value in (
            args.repo_url, args.image, args.dockerfile, args.compose_file))
        if selected > 1:
            raise exceptions.CommandError(
                'Choose one of --repo-url, --image, --dockerfile or '
                '--compose-file.')
        if args.dockerfile is not None:
            return 'dockerfile'
        if args.compose_file is not None:
            return 'compose'
        if args.repo_url is not None:
            return 'git'
        if args.image is not None or args.tag is not None:
            return 'image'
        raise exceptions.CommandError(
            'Specify --repo-url, --image, --dockerfile or --compose-file.')


class CreateService(_CreateWorkload):
    """Create and deploy a template or image service without waiting."""

    kinds = ('template', 'image')

    def source_type(self, args):
        if args.template is not None:
            if args.image is not None or args.tag is not None:
                raise exceptions.CommandError(
                    'Choose either --template or --image.')
            return 'template'
        if args.image is not None or args.tag is not None:
            return 'image'
        raise exceptions.CommandError('Specify --template or --image.')


def template_id(value):
    """Template slugs, e.g. postgresql (GET /v1/templates)."""
    if re.fullmatch(r'[A-Za-z0-9_-]{1,255}', value):
        return value
    raise argparse.ArgumentTypeError('Use a template ID such as postgresql.')


def positive_cpu(value):
    try:
        number = float(value)
        if math.isfinite(number) and number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use a positive finite CPU limit.')


class ListService(command.Lister):
    """List services; omit parent filters for all accessible services."""

    admin_scope = 'list'

    kind = 'service'

    def get_parser(self, prog_name):
        parser = utils.list_parser(super().get_parser(prog_name), 'service')
        parser.add_argument('--long', action='store_true',
                            help='Include server, kind and timestamps.')
        for parent in ('appgroup', 'environment'):
            parser.add_argument('--' + parent,
                                help='%s name or ID.' % (
                                    'App group' if parent == 'appgroup'
                                    else parent.capitalize()))
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        filters = {}
        group = None
        if args.appgroup is not None:
            group = utils.find_resource(
                api, args.appgroup, 'appgroup', details=True)
            filters['appgroup_id'] = group['id']
        if args.environment is not None:
            mismatch = exceptions.CommandError(
                "Environment '%s' does not belong to app group '%s'." %
                (args.environment, args.appgroup))
            try:
                environment = utils.find_resource(
                    api, args.environment, 'environment',
                    appgroup_id=filters.get('appgroup_id'), details=True)
            except exceptions.CommandError:
                if args.appgroup is None or utils.is_resource_id(
                        args.environment):
                    raise
                # A name missing from the chosen group may exist elsewhere.
                try:
                    utils.find_resource(api, args.environment, 'environment')
                except exceptions.CommandError as error:
                    if 'More than one' not in str(error):
                        raise
                raise mismatch
            filters['environment_id'] = environment['id']
            if (args.appgroup is not None and
                    environment['appgroup_id'] != filters['appgroup_id']):
                raise mismatch
        server_stack_id = None
        if args.server_id is not None and not utils.is_resource_id(
                args.server_id) and args.stack_id is None:
            if group is None and args.environment is not None:
                group = utils.find_resource(
                    api, environment['appgroup_id'], 'appgroup', details=True)
            if group is not None:
                server_stack_id = group['stack_id']
        filters['kind'] = self.kind
        rows = utils.list_data(self.app.client_manager.coast, '/services',
                               'services', args, filters,
                               server_stack_id=server_stack_id)
        fields = ('id', 'name', 'appgroup_id', 'environment_id', 'status')
        columns = ('ID', 'Name', 'App group ID', 'Environment ID', 'Status')
        if args.long:
            fields += ('server_id', 'kind', 'created_at', 'updated_at')
            columns += ('Server ID', 'Kind', 'Created at', 'Updated at')
        if any('health_check' in row for row in rows):
            fields += ('health_check',)
            columns += ('Health Check',)
        result = (columns,
                  (utils.get_dict_properties(row, fields) for row in rows))
        return utils.project_column(args, rows, result)


def server_floating_ip(api, stack_id, server_id):
    """Return the worker floating IP, or None when Coast has none.

    The show route is GET /v1/stacks/{stack_id}/servers/{server_id}
    (api-ref servers.rst). ``floating_ip`` is a public server field.
    A failed show reads the stack server list. Coast has no unnested
    /v1/servers/{server_id} route.
    """
    base = '/stacks/%s/servers' % (stack_id,)
    try:
        data = utils.request(api, '%s/%s' % (base, server_id), 'GET')
    except exceptions.CommandError:
        data = None
    if data is not None:
        return (data.get('server') or {}).get('floating_ip') or None
    try:
        rows = utils.collection_data(api, 'servers', path=base)
    except exceptions.CommandError:
        return None
    for row in rows:
        if row.get('id') == server_id:
            return row.get('floating_ip') or None
    return None


def worker_ip(api, row):
    """Return the public worker address for app or service show."""
    server_id = row.get('server_id')
    stack_id = row.get('stack_id')
    if server_id and stack_id:
        address = server_floating_ip(api, stack_id, server_id)
        if address:
            return address
    for record in row.get('dns_records') or []:
        if (isinstance(record, dict) and record.get('type') == 'A' and
                record.get('value')):
            return record['value']
    return None


class ShowService(command.ShowOne):
    """Show returned public app or service fields.

    Also prints url (default_url) and worker_ip. worker_ip is the
    worker floating IP, or the first dns_records A value.
    """

    def get_parser(self, prog_name):
        return service_parser(super().get_parser(prog_name),
                              app='App' in type(self).__name__)

    def take_action(self, args):
        api = self.app.client_manager.coast
        service_id = utils.find_resource(api, args.service, 'service')
        hooks = (webhook_urls(api, service_id, optional=True)
                 if self.webhooks else {})
        path = '/services/' + service_id
        data = utils.request(api, path, 'GET')
        row = dict(data['service'])
        row['url'] = row.get('default_url')
        row['worker_ip'] = worker_ip(api, row)
        for key, value in hooks.items():
            row['webhook_' + key] = value
        return utils.show(public_app(row))

    webhooks = False


def webhook_urls(api, service_id, regenerate=False, optional=False):
    """Return the app webhook URLs.

    Optional app display tolerates unavailable or forbidden webhooks.
    """
    path = '/services/%s/webhooks' % service_id
    try:
        if regenerate:
            result = api.request(path + '/regenerate', 'POST',
                                 webhooks_microversion='1.3')
        else:
            result = api.request(path, 'GET',
                                 webhooks_microversion='1.3')
    except ks_exc.HttpError as error:
        if optional and error.http_status in (403, 404, 406):
            return {}
        raise
    hooks = utils.resource_data(utils.response_data(result), 'webhooks')
    if not hooks.get('enabled'):
        return {} if not regenerate else {'enabled': False}
    return {key: hooks[key] for key in (
        'deploy', 'github', 'gitlab', 'bitbucket', 'gitea') if key in hooks}


def service_parser(parser, app=False):
    parser.add_argument('service', metavar='APP' if app else 'SERVICE',
                        help='App or service name or ID.')
    return parser


class DeleteService(command.Command):
    """Submit app or service deletion without waiting."""

    def get_parser(self, prog_name):
        return service_parser(super().get_parser(prog_name),
                              app='App' in type(self).__name__)

    def take_action(self, args):
        api = self.app.client_manager.coast
        path = '/services/' + utils.find_resource(api, args.service, 'service')
        utils.request(api, path, 'DELETE')


class ServiceAction(command.ShowOne):
    """Submit an app or service lifecycle action without waiting."""

    action = None

    def get_parser(self, prog_name):
        return service_parser(super().get_parser(prog_name),
                              app='App' in type(self).__name__)

    def take_action(self, args):
        api = self.app.client_manager.coast
        path = ('/services/' +
                utils.find_resource(api, args.service, 'service') + '/action')
        data = utils.request(api, path, 'POST', json={self.action: {}})
        return utils.show(utils.resource_data(data, 'service'))


class StartService(ServiceAction):
    """Start an app or service without waiting."""

    action = 'start'


class StopService(ServiceAction):
    """Stop an app or service without waiting."""

    action = 'stop'


class RestartService(ServiceAction):
    """Restart an app or service without waiting."""

    action = 'restart'


class ListApp(ListService):
    """List apps; omit parent filters to include all accessible apps."""

    kind = 'app'


class ShowApp(ShowService):
    """Show returned public app fields and, for members, webhook URLs."""

    webhooks = True


class ShowAppWebhook(command.ShowOne):
    """Show the app deploy URL and GitHub/GitLab/Bitbucket/Gitea webhooks."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument('--app', dest='service', required=True,
                            metavar='APP', help='App name or ID.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        target = utils.find_resource(api, args.service, 'service')
        urls = webhook_urls(api, target)
        if not urls:
            raise exceptions.CommandError(
                'Webhooks are disabled or not visible to you.')
        return utils.show(urls)


class RegenerateAppWebhook(ShowAppWebhook):
    """Issue a new webhook token. Old URLs stop working."""

    def take_action(self, args):
        api = self.app.client_manager.coast
        target = utils.find_resource(api, args.service, 'service')
        urls = webhook_urls(api, target, regenerate=True)
        if urls.get('enabled') is False:
            raise exceptions.CommandError('Webhooks are disabled.')
        return utils.show(urls)


class DeleteApp(DeleteService):
    """Submit app deletion without waiting."""


class StartApp(StartService):
    """Start an app without waiting."""


class StopApp(StopService):
    """Stop an app without waiting."""


class RestartApp(RestartService):
    """Restart an app without waiting."""


class SetService(command.ShowOne):
    """Change app or service settings.

    Name and description are saved immediately; other settings apply on the
    next deploy.
    """

    def get_parser(self, prog_name):
        parser = service_parser(super().get_parser(prog_name),
                                app='App' in type(self).__name__)
        parser.add_argument('--name', help='New name.')
        parser.add_argument('--description', help='New description.')
        parser.allow_abbrev = False
        domain = parser.add_mutually_exclusive_group()
        domain.add_argument('--enable-default-domain',
                            dest='default_domain_enabled',
                            action='store_true', default=None,
                            help='Enable the generated default domain.')
        domain.add_argument('--disable-default-domain',
                            dest='default_domain_enabled',
                            action='store_false',
                            help='Disable the generated default domain.')
        parser.add_argument('--image',
                            help='Container image reference.')
        parser.add_argument('--tag',
                            help='Container image tag.')
        ports = parser.add_mutually_exclusive_group()
        ports.add_argument(
            '--ports', dest='ports', metavar='PORTS',
            help='Port(s) the app listens on inside the container, '
                 'comma-separated (for example 80). '
                 'Required only when --health-check http or the app '
                 'has a domain. Use a blank value to clear.')
        ports.add_argument('--no-ports', action='store_true',
                           help='Clear the port(s).')
        cpu = parser.add_mutually_exclusive_group()
        cpu.add_argument('--cpu-limit', type=positive_cpu,
                         help='CPU limit in cores (positive number; '
                              'fractions accepted).')
        cpu.add_argument('--no-cpu-limit', action='store_true',
                         help='Clear the CPU limit.')
        memory = parser.add_mutually_exclusive_group()
        memory.add_argument('--memory-limit-mb',
                            type=utils.positive, metavar='MEMORY_LIMIT_MB',
                            help='Memory limit in MiB.')
        memory.add_argument('--no-memory-limit', action='store_true',
                            help='Clear the memory limit.')
        health_check_parser(parser, update=True)
        return parser

    def take_action(self, args):
        values = {key: getattr(args, key) for key in (
            'name', 'description', 'ports', 'cpu_limit', 'memory_limit_mb',
            'default_domain_enabled') if getattr(args, key) is not None}
        if args.no_ports or (args.ports is not None and
                             not args.ports.strip()):
            values['ports'] = None
        if args.no_cpu_limit:
            values['cpu_limit'] = None
        if args.no_memory_limit:
            values['memory_limit_mb'] = None
        git_fields = ('branch', 'build_pack', 'dockerfile_path',
                      'compose_path', 'base_directory')
        git_source = {key: getattr(args, key, None) for key in git_fields
                      if getattr(args, key, None) is not None}
        source = {key: getattr(args, key) for key in ('image', 'tag')
                  if getattr(args, key) is not None}
        source_options = {}
        if getattr(args, 'deploy_key', None) or getattr(
                args, 'deploy_key_file', None) is not None:
            source_options = source_version(
                self.app.client_manager.coast, '1.30')
            git_source['deploy_key'] = deploy_key(args)
        dockerfile = getattr(args, 'dockerfile', None)
        if dockerfile is not None:
            if source or git_source:
                raise exceptions.CommandError(
                    '--dockerfile cannot be combined with other source '
                    'options.')
            source_options = source_version(
                self.app.client_manager.coast, '1.31')
            content = read_source(dockerfile)
        if git_source and source:
            raise exceptions.CommandError(
                'Git source options cannot be combined with --image or --tag.')
        if source or git_source:
            values['source'] = source or git_source
        probe_options = health_check_values(args, values)
        probe_options.update(source_options)
        if not values and dockerfile is None:
            raise exceptions.CommandError('Nothing to change.')
        api = self.app.client_manager.coast
        path = '/services/' + utils.find_resource(api, args.service, 'service')
        if git_source:
            current = utils.resource_data(
                utils.request(api, path, 'GET', **source_options), 'service')
            if (current.get('source') or {}).get('type') != 'git':
                raise exceptions.CommandError(
                    'Git source options require a Git app.')
            old_key = ((current.get('source') or {}).get('deploy_key')
                       or {}).get('public_key')
        if dockerfile is not None:
            utils.request(api, path + '/dockerfile', 'PUT',
                          json={'dockerfile': {'text': content}},
                          **source_options)
            if not values:
                return utils.show({'id': path.rsplit('/', 1)[1]})
        data = utils.request(api, path, 'PATCH', json={'service': values},
                             **probe_options)

        resource = utils.resource_data(data, 'service')
        if resource.get('pending_changes'):
            self.app.stderr.write('Changes saved. Redeploy to apply.\n')
        if 'deploy_key' in git_source:
            resource, ready = wait_for_deploy_key(
                api, path, old_key, **source_options)
            if ready:
                key_hint(self.app, resource, args.service)
            else:
                self.app.stderr.write(
                    'The new deploy key is still being prepared. '
                    'See it later with `openstack coast app deploy-key show '
                    '%s`.\n' % args.service)
        return utils.show(public_app(resource))


class SetApp(SetService):
    """Change app settings.

    Name and description are saved immediately; other settings apply on the
    next deploy.
    """

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        source_options(parser)
        for field in ('branch', 'dockerfile-path', 'compose-path',
                      'base-directory'):
            parser.add_argument('--' + field, help=SOURCE_HELP[field])
        parser.add_argument('--build-pack', choices=(
            'nixpacks', 'dockerfile', 'compose', 'static'),
            help='Build method: nixpacks, dockerfile, compose or static.')
        return parser


class ShowAppDeployKey(command.Command):
    """Print only the app's public deploy key."""

    def get_parser(self, prog_name):
        return service_parser(super().get_parser(prog_name), app=True)

    def take_action(self, args):
        api = self.app.client_manager.coast
        options = source_version(api, '1.30')
        target = utils.find_resource(api, args.service, 'service')
        row = utils.request(api, '/services/' + target, 'GET', **options)
        key = (row['service'].get('source') or {}).get('deploy_key') or {}
        if not key.get('public_key'):
            raise exceptions.CommandError('This app has no deploy key.')
        self.app.stdout.write(key['public_key'] + '\n')


class ShowAppDockerfile(command.Command):
    """Print the stored Dockerfile."""

    def get_parser(self, prog_name):
        return service_parser(super().get_parser(prog_name), app=True)

    def take_action(self, args):
        api = self.app.client_manager.coast
        options = source_version(api, '1.31')
        target = utils.find_resource(api, args.service, 'service')
        data = utils.request(api, '/services/' + target + '/dockerfile',
                             'GET', **options)
        self.app.stdout.write(data['dockerfile']['text'])
