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

"""Shared mechanics for explicit API 1.24 command definitions."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib import parse

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def boolean(value):
    if value not in ('on', 'off'):
        raise argparse.ArgumentTypeError('Use on or off.')
    return value == 'on'


# Help is explicit: new API fields must describe their customer-facing meaning.
OPTION_HELP = {
    'size_gb': 'Volume size in GiB (positive integer, at least 1).',
    'type': 'Storage mount type: volume or bind.',
    'service_id': 'App or service name or ID.',
    'server_id': 'Server name or ID.',
    'stack_id': 'Stack name or ID.',
    'container_path': 'Mount destination path inside the container.',
    'host_path': 'Source path on the host for a bind mount.',
    'read_only': 'Mount access: on for read-only, off for read-write.',
    'volume_type': 'Operator-provided block storage volume type.',
    'mount_path': 'Volume mount path on the server.',
    'resource_name': 'Template container name; omit for a single-container '
                     'template. Invalid for non-template apps.',
    'volume_ids': 'Volume UUID to export; repeat for more, omit for all.',
    'default_domain': 'Default domain suffix.',
    'dns_zone_id': 'DNS zone ID.',
    'dns_trust_id': 'Delegated DNS trust ID.',
    'image_id': 'Published app engine server image UUID.',
    'trust_id': 'Delegated DNS trust UUID; omit to list the caller project.',
    'resource_type': 'Filter events by resource type.',
    'resource_id': 'Filter events by resource UUID.',
    'action': 'Filter events by action name.',
    'outcome': 'Filter events by outcome.',
    'request_id': 'Filter events by request ID.',
    'mode': 'Restore mode: new_copy or overwrite.',
    'confirmation': 'Current service or database name for overwrite restore.',
    'enabled': 'Scheduled backups: on or off; new policies default to off.',
    'engine': 'Database engine: postgres, mysql, mariadb or mongodb; required '
              'for the first policy update.',
    'container': 'Database Compose container name; required for the first '
                 'policy update.',
    'hour': 'UTC backup hour (0-23; new policy default: 2).',
    'minute': 'UTC backup minute (0-59; new policy default: 0).',
    'keep_daily': 'Daily backups to retain (0-365; new policy default: 14).',
    'keep_weekly': 'Weekly backups to retain (0-104; new policy default: 8).',
    'tail': 'Recent container log lines (0-10000; API default: 200). '
            'Zero returns no lines.',
    'since': 'Read container logs after this ISO timestamp; naive times '
             'mean UTC. Omit for no time filter.',
    'range': 'Metrics time range: 15m, 1h, 6h, 24h, 7d or 30d; omit for '
             'the API default of 1h.',
}


OPTION_CHOICES = {
    'type': ('bind', 'volume'),
    'mode': ('new_copy', 'overwrite'),
    'engine': ('postgres', 'mysql', 'mariadb', 'mongodb'),
    'range': ('15m', '1h', '6h', '24h', '7d', '30d'),
}


class ResourceCommand:
    """Translate declared arguments into one existing API request."""

    route = ''
    method = 'GET'
    envelope = None
    options = ()
    required = ()
    action = None
    paginated = False
    list_filters = None
    fields = ('id', 'name', 'status')
    formatters = {}
    query = ()
    raw_body = False
    input_field = None
    file_required = True
    suppress_content = False
    nullable = ()

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        for name in re.findall(r'{(.*?)}', self.route):
            noun = name.removesuffix('_id')
            parent = (name == 'stack_id' and self.envelope != 'stack' or
                      name in ('service_id', 'project_id'))
            if parent:
                parser.add_argument('--' + noun, dest=name, required=True,
                                    metavar=noun.upper(),
                                    help=('Project UUID.'
                                          if noun == 'project' else
                                          'App or service name or ID.'
                                          if noun == 'service' else
                                          'Stack name or ID.'))
            else:
                named = noun in ('stack', 'server', 'appgroup', 'environment')
                metavar = noun if named or noun == 'project' else name
                parser.add_argument(name, metavar=metavar.upper(),
                                    help='%s %s.' % (
                                        noun.replace('appgroup', 'app group')
                                        .replace('_', ' ').capitalize(),
                                        'name or ID' if named else 'ID'))
        for name in self.options + self.query:
            kind = str
            if name in ('hour', 'minute', 'keep_daily',
                        'keep_weekly', 'tail'):
                kind = utils.nonnegative
            if name == 'size_gb':
                kind = utils.positive
            if name in ('read_only', 'enabled'):
                kind = boolean
            option = {'trust_id': 'dns-trust', 'volume_ids': 'volume'}.get(
                name, (name.removesuffix('_id') if name in (
                    'stack_id', 'server_id', 'service_id', 'dns_zone_id',
                    'dns_trust_id', 'image_id') else name).replace('_', '-'))
            kwargs = {}
            if name in OPTION_CHOICES:
                kwargs['choices'] = OPTION_CHOICES[name]
            if name == 'volume_ids':
                kwargs = {'action': 'append', 'metavar': 'VOLUME_ID',
                          'type': utils.resource_id}
            else:
                kwargs['type'] = kind
                if name in ('dns_zone_id', 'dns_trust_id', 'trust_id'):
                    kwargs['type'] = utils.resource_id
                if name.endswith('_id'):
                    named = name in ('stack_id', 'server_id', 'service_id')
                    kwargs['metavar'] = (
                        name.removesuffix('_id') if named else
                        'dns_trust_id' if name == 'trust_id' else name).upper()
            help_text = OPTION_HELP.get(name)
            if name in ('name', 'description'):
                noun = self.envelope.replace('appgroup', 'app group')
                noun = noun.replace('_', ' ')
                if self.method == 'POST' and name == 'name':
                    help_text = 'Name for the new %s.' % noun
                else:
                    help_text = '%s %s.' % (noun.capitalize(), name)
            if name == 'size_gb' and self.method == 'PATCH':
                help_text += (' The new size must be larger than '
                              'the current one.')
            if name == 'name' and self.envelope == 'restore':
                help_text = ('Name for the new service or database; required '
                             'for the default new_copy mode and not allowed '
                             'with overwrite.')
            target = parser
            if name in self.nullable:
                target = parser.add_mutually_exclusive_group()
                target.add_argument(
                    '--no-' + option, dest='clear_' + name,
                    action='store_true', help='Clear the %s.' % (
                        option.replace('-', ' ')))
                if kind is str and not name.endswith('_id'):
                    kwargs['type'] = utils.domain_suffix
            target.add_argument('--' + option, dest=name,
                                required=name in self.required,
                                help=help_text, **kwargs)
        if self.input_field:
            parser.add_argument('--file', required=self.file_required,
                                help=('JSON environment mapping; - reads '
                                      'stdin.'
                                      if self.input_field == 'env' else
                                      'UTF-8 input file; - reads stdin.'))
        if self.paginated:
            utils.list_parser(parser)
        if self.list_filters:
            from coastclient.osc.v1 import filters
            filters.add_options(parser, self.list_filters)
        return parser

    def option_values(self, args):
        return {key: getattr(args, key) for key in self.options
                if getattr(args, key) is not None}

    def prepare_rows(self, api, args, rows):
        return rows

    def take_action(self, args):
        api = self.app.client_manager.coast
        utils.resolve_references(api, args)
        values = self.option_values(args)
        for key in self.nullable:
            if getattr(args, 'clear_' + key):
                values[key] = None
        if self.input_field and args.file is not None:
            try:
                content = (sys.stdin.read() if args.file == '-'
                           else Path(args.file).read_text(encoding='utf-8'))
            except (OSError, UnicodeError):
                raise exceptions.CommandError(
                    "Unable to read file '%s'." % args.file) from None
            if self.input_field == 'env':
                try:
                    values = json.loads(content)
                except ValueError:
                    raise exceptions.CommandError('Use a JSON env mapping.')
                if not isinstance(values, dict):
                    raise exceptions.CommandError('Use a JSON env mapping.')
            else:
                values[self.input_field] = content
        if (self.method in ('PATCH', 'PUT') and not values
                and self.input_field != 'env'):
            raise exceptions.CommandError('Nothing to change.')
        path = self.route.format(**{
            key: parse.quote(str(value), safe='')
            for key, value in vars(args).items()})
        params = {key: getattr(args, key) for key in self.query
                  if getattr(args, key) is not None}
        if isinstance(self, command.Lister) and self.paginated:
            rows = utils.list_data(api, path, self.envelope, args, params)
            data = None
        else:
            if getattr(self, 'admin_scope', None) == 'read':
                params.update(utils.scope_params(args))
            kwargs = {'params': params} if params else {}
            if self.method in ('POST', 'PATCH', 'PUT'):
                if self.action:
                    kwargs['json'] = {self.action: values}
                elif self.raw_body:
                    kwargs['json'] = values
                else:
                    kwargs['json'] = {self.envelope: values}
            data = utils.request(api, path, self.method, **kwargs)
            if isinstance(self, command.Lister):
                rows = data.get(self.envelope, []) if self.envelope else []
        if isinstance(self, command.Lister):
            rows = self.prepare_rows(api, args, rows)
            result = (tuple(utils.label(field) for field in self.fields),
                      (utils.get_dict_properties(
                          row, self.fields, formatters=self.formatters)
                       for row in rows))
            return utils.project_column(args, rows, result)
        if self.input_field == 'env':
            from coastclient.osc.v1 import env
            env.output(self.app, data)
            return
        if self.suppress_content:
            row = utils.resource_data(data, self.envelope)
            return utils.show({key: value for key, value in row.items()
                               if key != self.input_field} or {'saved': True})
        if isinstance(self, command.ShowOne):
            return utils.show(utils.resource_data(data, self.envelope))
