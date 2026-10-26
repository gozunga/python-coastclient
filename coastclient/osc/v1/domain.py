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

"""Service-scoped domain commands; Coast performs DNS verification."""
import argparse

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import env
from coastclient.osc.v1 import utils


def collection(api, args):
    return ('/services/' + utils.find_resource(api, args.service, 'service') +
            '/domains')


def item_parser(parser):
    env.scoped(parser)
    parser.add_argument('domain', metavar='DOMAIN',
                        help='Domain name (FQDN) or ID.')
    return parser


def find_domain(api, base, value):
    """Resolve a domain FQDN in one service; UUIDs need no lookup."""
    try:
        return utils.resource_id(value)
    except argparse.ArgumentTypeError:
        pass
    wanted = value.strip().rstrip('.').lower()
    args = argparse.Namespace(limit=None, marker=None, sort_key=None,
                              sort_dir=None)
    for row in utils.list_data(api, base, 'domains', args):
        if (row.get('fqdn') or '').lower() == wanted:
            return row['id']
    raise exceptions.CommandError(utils.not_found('domain', value))


def item_path(api, args):
    base = collection(api, args)
    return base + '/' + find_domain(api, base, args.domain)


def tls_argument(parser, required=False):
    parser.add_argument(
        '--tls', required=required, choices=('letsencrypt', 'none'),
        help='Select the certificate mode.')
    return parser


def routing_arguments(parser):
    parser.allow_abbrev = False
    primary = parser.add_mutually_exclusive_group()
    primary.add_argument('--primary', action='store_true', default=None,
                         help='Make this the primary domain.')
    primary.add_argument('--no-primary', dest='primary', action='store_false',
                         help='Stop using this as the primary domain.')
    parser.add_argument('--dns-trust', dest='dns_trust_id', metavar='TRUST_ID',
                        help='Delegated DNS trust ID for managing records.')
    parser.add_argument('--record-type', choices=('A', 'AAAA', 'CNAME'),
                        help='DNS record type for automatic DNS management.')
    parser.add_argument('--zone', dest='zone_id', metavar='DNS_ZONE_ID',
                        help='DNS zone ID for automatic DNS management.')
    parser.add_argument('--host',
                        help='Host label within the selected DNS zone.')
    options = parser.add_mutually_exclusive_group()
    options.add_argument('--auto-dns', action='store_true',
                         help='Let Coast manage DNS records in the '
                              'selected zone.')
    options.add_argument('--no-auto-dns', dest='auto_dns',
                         action='store_false',
                         help='Manage DNS records yourself using the returned '
                              'instructions.')
    parser.set_defaults(auto_dns=None)
    return tls_argument(parser)


def routing_values(args):
    return {key: getattr(args, key) for key in (
        'primary', 'zone_id', 'host', 'auto_dns', 'tls',
        'dns_trust_id', 'record_type')
        if getattr(args, key) is not None}


class AddDomain(command.ShowOne):
    """Add a domain and print DNS instructions without waiting."""

    def get_parser(self, prog_name):
        parser = env.scoped(super().get_parser(prog_name))
        parser.add_argument('name', metavar='DOMAIN', nargs='?',
                            help='FQDN; omit with --host and --zone.')
        return routing_arguments(parser)

    def take_action(self, args):
        api = self.app.client_manager.coast
        values = routing_values(args)
        if args.name is not None:
            values['fqdn'] = args.name
        if args.name is None and (args.host is None or not args.zone_id):
            raise exceptions.CommandError('Use DOMAIN or --host and --zone.')
        extra = ({'domain_tls_microversion': '1.2'}
                 if args.tls is not None else {})
        data = utils.request(api, collection(api, args),
                             'POST', json={'domain': values}, **extra)
        return utils.show(utils.resource_data(data, 'domain'))


class SetDomain(command.ShowOne):
    """Update domain routing, primary selection or TLS without waiting."""

    def get_parser(self, prog_name):
        parser = routing_arguments(item_parser(super().get_parser(prog_name)))
        parser.add_argument('--fqdn', help='New fully qualified domain name.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        values = routing_values(args)
        if args.fqdn is not None:
            values['fqdn'] = args.fqdn
        if not values:
            raise exceptions.CommandError('Nothing to change.')
        data = utils.request(
            api, item_path(api, args), 'PATCH',
            json={'domain': values},
            **({'domain_tls_microversion': '1.2'}
               if args.tls is not None else {}))
        return utils.show(utils.resource_data(data, 'domain'))


class ListDomain(command.Lister):
    """List domains in an app or service."""

    admin_scope = 'list'

    def get_parser(self, prog_name):
        return utils.list_parser(
            env.scoped(super().get_parser(prog_name)), 'domain')

    def take_action(self, args):
        api = self.app.client_manager.coast
        rows = utils.list_data(api, collection(api, args),
                               'domains', args)
        result = (('ID', 'FQDN', 'Status', 'TLS', 'Primary'),
                  (utils.get_dict_properties(
                      row, ('id', 'fqdn', 'status', 'tls', 'primary'))
                   for row in rows))
        return utils.project_column(args, rows, result)


class ShowDomain(command.ShowOne):
    """Show domain routing, TLS, status and DNS records to publish."""

    def get_parser(self, prog_name):
        return item_parser(super().get_parser(prog_name))

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api, item_path(api, args), 'GET')
        return utils.show(data['domain'])


class VerifyDomain(command.ShowOne):
    """Submit verification without a local DNS check or wait."""

    def get_parser(self, prog_name):
        return item_parser(super().get_parser(prog_name))

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api,
                             item_path(api, args) + '/action', 'POST',
                             json={'verify': {}})
        return utils.show(utils.resource_data(data, 'domain'))


class RemoveDomain(command.Command):
    """Remove a domain without waiting."""

    def get_parser(self, prog_name):
        return item_parser(super().get_parser(prog_name))

    def take_action(self, args):
        api = self.app.client_manager.coast
        utils.request(api, item_path(api, args),
                      'DELETE')
