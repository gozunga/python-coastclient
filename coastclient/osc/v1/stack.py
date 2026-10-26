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

"""Stack provisioning and public resource commands."""
import argparse
import json

from keystoneauth1 import exceptions as ks_exc
from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def worker(value):
    result = {}
    for item in value.split(','):
        key, separator, selection = item.partition('=')
        if (not separator or not selection or key in result or
                key not in ('kind', 'plan', 'image')):
            raise argparse.ArgumentTypeError(
                'Use kind=vm|baremetal,plan=PLAN[,image=IMAGE].')
        result[key] = selection
    if (not {'kind', 'plan'} <= result.keys() or
            result['kind'] not in ('vm', 'baremetal')):
        raise argparse.ArgumentTypeError(
            'Use kind=vm|baremetal,plan=PLAN[,image=IMAGE].')
    return result


class CreateStack(command.ShowOne):
    """Create a stack without waiting for provisioning."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('name', metavar='NAME',
                            help='Name for the new stack.')
        parser.add_argument('--plan', required=True,
                            help='Operator plan name from '
                                 'openstack coast plan list.')
        parser.add_argument('--description',
                            help='Stack description.')
        parser.add_argument('--worker', action='append', type=worker,
                            help='Add a worker using '
                                 'kind=vm|baremetal,plan=PLAN[,image=IMAGE].')
        parser.add_argument('--data-volume-gb', type=utils.positive,
                            help='Data volume size in GiB (positive integer); '
                                 'omit for the operator default.')
        parser.add_argument('--build-server', action='store_true',
                            default=None,
                            help='Provision a dedicated build server; omitted '
                                 'by default.')
        parser.add_argument('--image',
                            help='App engine server image name or ID '
                                 '(API 1.1+); omit for the operator default.')
        parser.add_argument('--build-server-plan',
                            help='Build VM plan (API 1.1+); requires '
                                 '--build-server. Omit to select the unique '
                                 'build-compatible VM plan.')
        parser.add_argument('--build-server-image',
                            help='Build VM image name or ID (API 1.1+); '
                                 'requires --build-server. Omit for the '
                                 'operator default.')
        for name, help_text in (
                ('default_domain', 'Default domain suffix; omit to use the '
                 'selected zone name, if any'),
                ('dns_zone_id', 'DNS zone UUID; omit to select the most '
                 'specific project zone matching the domain, if any'),
                ('dns_trust_id', 'Delegated DNS trust UUID; omit to use '
                 'the caller project')):
            parser.add_argument(
                '--' + name.removesuffix('_id').replace('_', '-'),
                dest=name, help=help_text +
                ' (API 1.22+).',
                type=(utils.domain_suffix if name == 'default_domain'
                      else utils.resource_id))
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        values = {key: getattr(args, key) for key in (
            'name', 'plan', 'description', 'data_volume_gb', 'build_server',
            'image', 'build_server_plan', 'build_server_image',
            'default_domain', 'dns_zone_id', 'dns_trust_id')
            if getattr(args, key) is not None}
        if args.worker is not None:
            values['workers'] = args.worker
        selections = any(key in values for key in (
            'image', 'build_server_plan', 'build_server_image'))
        if api.microversion == '1.0' and (
                selections or any('image' in item
                                  for item in args.worker or [])):
            raise exceptions.CommandError(
                'Image and build selections require '
                '--os-coast-api-microversion 1.1.')
        if api.microversion == '1.1' and (
                not args.worker or
                any(item['kind'] != 'vm' for item in args.worker)):
            raise exceptions.CommandError('Microversion 1.1 requires '
                                          'one or more VM --worker.')
        if not args.build_server and any(key in values for key in (
                'build_server_plan', 'build_server_image')):
            raise exceptions.CommandError('Build selections require '
                                          '--build-server.')
        data = utils.request(api, '/stacks', 'POST', json={'stack': values})
        return utils.show(data['stack'])


class ListStack(command.Lister):
    """List stacks, including all API pages."""

    admin_scope = 'list'

    def get_parser(self, prog_name):
        parser = utils.list_parser(super().get_parser(prog_name), 'stack')
        parser.add_argument('--long', action='store_true',
                            help='Show description and default domain.')
        return parser

    def take_action(self, args):
        rows = utils.list_data(self.app.client_manager.coast,
                               '/stacks', 'stacks', args)
        fields = ('id', 'name', 'status', 'plan', 'driver', 'engine_version',
                  'created_at')
        columns = ('ID', 'Name', 'Status', 'Plan', 'Driver', 'Engine Version',
                   'Created')
        if args.long:
            fields += ('description', 'default_domain')
            columns += ('Description', 'Default domain')
        if not any('driver' in row for row in rows):
            fields = tuple(field for field in fields if field != 'driver')
            columns = tuple(column for column in columns if column != 'Driver')
        result = columns, (utils.get_dict_properties(row, fields)
                           for row in rows)
        return utils.project_column(args, rows, result)


class ShowStack(command.ShowOne):
    """Show only the stack fields returned by Coast."""

    admin_scope = 'read'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('stack',
                            metavar='STACK', help='Stack name or ID.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        data = utils.request(self.app.client_manager.coast,
                             '/stacks/' + args.stack, 'GET',
                             params=utils.scope_params(args))
        return utils.show(data['stack'])


class DeleteStack(command.Command):
    """Submit stack deletion without waiting for completion."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('stack',
                            metavar='STACK', help='Stack name or ID.')
        parser.add_argument('--snapshot-volumes', action='store_true',
                            default=None,
                            help='Snapshot data volumes before deleting '
                                 'the stack.')
        parser.add_argument('--snapshot-retention-days',
                            type=utils.nonnegative,
                            help='Snapshot retention in days (integer >= 0; '
                                 'requires --snapshot-volumes).')
        parser.add_argument('--export-before-delete', action='store_true',
                            default=None,
                            help='Export stack data before deletion.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        if (args.snapshot_retention_days is not None and
                not args.snapshot_volumes):
            raise exceptions.CommandError('--snapshot-retention-days '
                                          'requires --snapshot-volumes.')
        options = {key: getattr(args, key) for key in (
            'snapshot_volumes', 'snapshot_retention_days',
            'export_before_delete')
            if getattr(args, key) is not None}
        utils.request(self.app.client_manager.coast,
                      '/stacks/' + args.stack, 'DELETE',
                      **({'json': options} if options else {}))


class ListStackEvent(command.Lister):
    """List stack events, newest first (Coast API 1.8 or later)."""

    admin_scope = 'read'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('stack', metavar='STACK', help='Stack name or ID.')
        utils.event_list_parser(parser, detail=True)
        parser.add_argument('--resource-type',
                            choices=('stack', 'server', 'app', 'service'),
                            help='Filter events by resource type. App and '
                                 'service require Coast API 1.9 or later.')
        return parser

    def take_action(self, args):
        try:
            api = self.app.client_manager.coast
        except ks_exc.DiscoveryFailure as error:
            if 'outside discovery bounds' not in str(error):
                raise
            raise exceptions.CommandError(
                'Stack events need Coast API 1.8 or later.') from None
        if tuple(map(int, api.microversion.split('.'))) < (1, 8):
            raise exceptions.CommandError(
                'Stack events need Coast API 1.8 or later.')
        if (args.resource_type in ('app', 'service') and
                tuple(map(int, api.microversion.split('.'))) < (1, 9)):
            raise exceptions.CommandError(
                'App and service event filters need Coast API 1.9 or later.')
        stack_id = utils.find_resource(api, args.stack, 'stack')
        paging = argparse.Namespace(limit=args.limit, marker=args.marker,
                                    sort_key=None, sort_dir=None)
        filters = ({'resource_type': args.resource_type}
                   if args.resource_type else {})
        if args.outcome is not None:
            filters['outcome'] = args.outcome
        filters.update(utils.scope_params(args))
        rows = utils.list_data(
            api, '/stacks/' + stack_id + '/events', 'events', paging,
            filters, total_limit=args.limit)
        if args.all_projects:
            stack = utils.request(
                api, '/stacks/' + stack_id, 'GET',
                params=utils.scope_params(args))['stack']
            rows = [dict(row, project_id=stack.get('project_id'))
                    for row in rows]
        fields = ('created_at', 'resource_type', 'resource_id', 'action',
                  'outcome', 'message')
        columns = ('Time', 'Resource Type', 'Resource ID', 'Action',
                   'Outcome', 'Message')
        if args.long:
            fields += ('id', 'stack_id', 'request_id', 'detail')
            columns += ('ID', 'Stack ID', 'Request ID', 'Detail')
            rows = [dict(row, detail=json.dumps(row.get('detail'),
                                                sort_keys=True))
                    for row in rows]
        result = columns, (utils.get_dict_properties(row, fields)
                           for row in rows)
        return utils.project_column(args, rows, result)


class UpgradeStack(command.ShowOne):
    """Upgrade the app engine server image without waiting for completion."""

    route = '/stacks/{stack_id}/action'
    method = 'POST'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('stack', metavar='STACK', help='Stack name or ID.')
        parser.add_argument('--image', dest='image',
                            required=True, type=utils.resource_id,
                            help='Published app engine server image UUID '
                                 '(Glance ID; names are not resolved).')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        api = self.app.client_manager.coast
        return utils.show(utils.api_call(api.stacks.upgrade,
                                         args.stack, args.image))


class RollbackStackUpgrade(UpgradeStack):
    """Roll back the app engine image upgrade without waiting."""

    def get_parser(self, prog_name):
        parser = command.ShowOne.get_parser(self, prog_name)
        parser.add_argument('stack', metavar='STACK', help='Stack name or ID.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        api = self.app.client_manager.coast
        return utils.show(utils.api_call(api.stacks.upgrade_rollback,
                                         args.stack))


class ResizeStack(command.ShowOne):
    """Replace every worker one at a time; larger flavors only.

    Submit without waiting and show the stack. Use server resize to change
    a single server. Requires Coast API 1.26 or later.
    """

    route = '/stacks/{stack_id}/action'
    method = 'POST'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('stack', metavar='STACK', help='Stack name or ID.')
        parser.add_argument('--flavor', required=True, metavar='FLAVOR',
                            help='Flavor name from openstack coast flavor '
                                 'list (larger flavors only).')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        utils.resolve_references(api, args)
        stack_id = args.stack
        data = utils.request(api, '/stacks/' + stack_id + '/action', 'POST',
                             json={'resize_workers': {'flavor': args.flavor}})
        return utils.show(data['stack'])
