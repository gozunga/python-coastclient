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

"""Stack ingress rule commands (Coast API 1.25)."""
import argparse

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def port_range(value):
    parts = value.split(':')
    if (len(parts) in (1, 2) and
            all(part.isascii() and part.isdecimal() for part in parts)):
        low, high = int(parts[0]), int(parts[-1])
        if 1 <= low <= high <= 65535:
            return low, high
    raise argparse.ArgumentTypeError(
        'Use a port N or range N:M between 1 and 65535.')


def rule_id(value):
    if value in ('managed-http', 'managed-https', 'managed-ssh'):
        return value
    return utils.resource_id(value)


class IngressRule:
    route = '/stacks/{stack_id}/ingress_rules'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('--stack', required=True, metavar='STACK',
                            help='Stack name or ID.')
        return parser

    def api_path(self, args):
        api = self.app.client_manager.coast
        if tuple(map(int, api.microversion.split('.'))) < (1, 25):
            raise exceptions.CommandError(
                'Ingress rules need Coast API 1.25 or later.')
        return api, '/stacks/' + args.stack + '/ingress_rules'


class ListIngressRule(IngressRule, command.Lister):
    """List custom and Coast managed worker ingress rules."""

    method = 'GET'

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        api, path = self.api_path(args)
        rows = utils.request(api, path, self.method)['ingress_rules']
        fields = ('id', 'protocol', 'port_range_min', 'port_range_max',
                  'source', 'description', 'managed')
        columns = ('ID', 'Protocol', 'Port range min', 'Port range max',
                   'Source', 'Description', 'Managed')
        return columns, (utils.get_dict_properties(row, fields)
                         for row in rows)


class CreateIngressRule(IngressRule, command.ShowOne):
    """Create worker ingress, applied at the next enabled repair sweep."""

    method = 'POST'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('--protocol', required=True,
                            choices=('tcp', 'udp', 'icmp'),
                            help='Ingress protocol: tcp, udp or icmp.')
        parser.add_argument('--port', type=port_range, metavar='N[:M]',
                            help='TCP/UDP port N or range N:M (1-65535); '
                                 'omit for ICMP.')
        parser.add_argument('--source',
                            help='Source IPv4 CIDR (API default: 0.0.0.0/0).')
        parser.add_argument('--description',
                            help='Ingress rule description.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        if (args.protocol == 'icmp') != (args.port is None):
            raise exceptions.CommandError(
                'TCP/UDP require --port; ICMP requires omitting --port.')
        values = {key: getattr(args, key)
                  for key in ('protocol', 'source', 'description')
                  if getattr(args, key) is not None}
        if args.port is not None:
            values.update(port_range_min=args.port[0],
                          port_range_max=args.port[1])
        api, path = self.api_path(args)
        data = utils.request(api, path, self.method,
                             json={'ingress_rule': values})
        return utils.show(data['ingress_rule'])


class DeleteIngressRule(IngressRule, command.Command):
    """Delete a custom rule at the next enabled repair sweep."""

    method = 'DELETE'
    route = IngressRule.route + '/{rule_id}'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('rule', type=rule_id, metavar='RULE_ID',
                            help='Custom ingress rule UUID.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        api, path = self.api_path(args)
        utils.request(api, path + '/' + args.rule, self.method)
