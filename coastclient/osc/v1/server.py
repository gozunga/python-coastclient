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

"""Managed server provisioning commands scoped to a stack."""
from cliff import columns

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def scoped(parser):
    parser.allow_abbrev = False
    parser.add_argument('--stack', required=True,
                        metavar='STACK', help='Stack name or ID.')
    return parser


class AddServer(command.ShowOne):
    """Add a managed server without waiting for provisioning."""

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('name', metavar='NAME', nargs='?',
                            help='Server name (default: next <role>-N).')
        parser.add_argument('--kind', required=True,
                            choices=('vm', 'baremetal'),
                            help='Server kind: vm or baremetal.')
        parser.add_argument('--role', required=True,
                            choices=('worker', 'build'),
                            help='Server role: worker or build.')
        parser.add_argument('--plan', required=True,
                            help='Operator plan name from '
                                 'openstack coast plan list.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        values = {key: getattr(args, key) for key in (
            'name', 'kind', 'role', 'plan') if getattr(args, key) is not None}
        data = utils.request(self.app.client_manager.coast,
                             '/stacks/' + args.stack + '/servers', 'POST',
                             json={'server': values})
        return utils.show(data['server'])


class ListServer(command.Lister):
    """List all managed servers in a stack."""

    admin_scope = 'list'

    def get_parser(self, prog_name):
        return utils.list_parser(
            scoped(super().get_parser(prog_name)), 'server')

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        rows = utils.list_data(self.app.client_manager.coast,
                               '/stacks/' + args.stack + '/servers',
                               'servers', args)
        fields = ('id', 'name', 'kind', 'role', 'status', 'floating_ip',
                  'hostname', 'flavor')
        result = (('ID', 'Name', 'Kind', 'Role', 'Status', 'Floating IP',
                   'Hostname', 'Flavor'),
                  (utils.get_dict_properties(
                      row, fields, formatters={'flavor': FlavorColumn})
                   for row in rows))
        return utils.project_column(args, rows, result)


class RemoveServer(command.Command):
    """Submit managed server removal without waiting for completion."""

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('server',
                            metavar='SERVER', help='Server name or ID.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        utils.request(self.app.client_manager.coast,
                      '/stacks/' + args.stack + '/servers/' +
                      args.server,
                      'DELETE')


class ResizeServer(command.ShowOne):
    """Resize a managed server to a larger flavor without waiting."""

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('server',
                            metavar='SERVER', help='Server name or ID.')
        parser.add_argument('--flavor', required=True,
                            help='Plan or flavor name from '
                                 'openstack coast flavor list '
                                 '(must grow).')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        data = utils.request(
            self.app.client_manager.coast,
            '/stacks/' + args.stack + '/servers/' + args.server + '/action',
            'POST', json={'resize': {'flavor': args.flavor}},
            server_resize_microversion='1.26')
        return utils.show(data['server'])


def flavor_name(value):
    if isinstance(value, dict):
        return value.get('name') or value.get('id', '')
    return value or ''


class FlavorColumn(columns.FormattableColumn):
    """Keep structured flavor data in machine output and units in tables."""

    def human_readable(self):
        value = self._value
        if not isinstance(value, dict):
            return flavor_name(value)
        parts = [flavor_name(value)]
        for field, unit in (('vcpus', 'vCPUs'), ('ram', 'MiB RAM'),
                            ('disk', 'GiB disk')):
            if value.get(field) is not None:
                parts.append('%s %s' % (value[field], unit))
        return ', '.join(parts)
