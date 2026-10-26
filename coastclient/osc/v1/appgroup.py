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

"""App group commands using the public Coast API."""

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def scoped(parser):
    parser.allow_abbrev = False
    parser.add_argument('--stack', required=True,
                        metavar='STACK', help='Stack name or ID.')
    return parser


class CreateAppgroup(command.ShowOne):
    'Create an app group without waiting for completion.'

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('name', metavar='NAME',
                            help='Name for the new app group.')
        parser.add_argument('--description',
                            help='App group description.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        values = {'name': args.name}
        if args.description is not None:
            values['description'] = args.description
        data = utils.request(self.app.client_manager.coast,
                             '/stacks/' + args.stack + '/appgroups', 'POST',
                             json={'appgroup': values})
        return utils.show(data['appgroup'])


class ListAppgroup(command.Lister):
    'List all app groups in the selected stack.'

    admin_scope = 'list'

    def get_parser(self, prog_name):
        return utils.list_parser(
            scoped(super().get_parser(prog_name)), 'appgroup')

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        rows = utils.list_data(self.app.client_manager.coast,
                               '/stacks/' + args.stack + '/appgroups',
                               'appgroups', args)
        fields = ('id', 'name', 'stack_id', 'status')
        result = (('ID', 'Name', 'Stack ID', 'Status'),
                  (utils.get_dict_properties(row, fields) for row in rows))
        return utils.project_column(args, rows, result)


class ShowAppgroup(command.ShowOne):
    'Show app group fields and current children.'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('appgroup', metavar='APPGROUP',
                            help='App group name or ID.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        api = self.app.client_manager.coast
        resource_id = utils.find_resource(api, args.appgroup, 'appgroup')
        data = utils.request(api, '/appgroups/' + resource_id,
                             'GET')['appgroup']
        filters = {'appgroup_id': resource_id}
        data.update(utils.workload_children(api, filters))
        data['environments'] = utils.child_summary(
            utils.collection_data(
                api, 'environments',
                path='/appgroups/' + resource_id + '/environments'))
        return utils.show(data)


class DeleteAppgroup(command.Command):
    'Submit app group deletion without waiting for completion.'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('appgroup', metavar='APPGROUP',
                            help='App group name or ID.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        api = self.app.client_manager.coast
        resource_id = utils.find_resource(api, args.appgroup, 'appgroup')
        utils.request(api, '/appgroups/' + resource_id, 'DELETE')
