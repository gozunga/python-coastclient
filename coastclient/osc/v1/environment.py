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

"""Environment commands using the public Coast API."""

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def scoped(parser):
    parser.allow_abbrev = False
    parser.add_argument('--appgroup', required=True,
                        metavar='APPGROUP',
                        help='App group name or ID.')
    return parser


class CreateEnvironment(command.ShowOne):
    """Create an environment without waiting for completion."""

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('name', metavar='NAME',
                            help='Name for the new environment.')
        parser.add_argument('--description',
                            help='Environment description.')
        return parser

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        values = {'name': args.name}
        if args.description is not None:
            values['description'] = args.description
        data = utils.request(self.app.client_manager.coast,
                             '/appgroups/' + args.appgroup +
                             '/environments',
                             'POST',
                             json={'environment': values})
        return utils.show(data['environment'])


class ListEnvironment(command.Lister):
    'List all environments in the selected app group.'

    admin_scope = 'list'

    def get_parser(self, prog_name):
        return utils.list_parser(
            scoped(super().get_parser(prog_name)), 'environment')

    def take_action(self, args):
        utils.resolve_references(self.app.client_manager.coast, args)
        rows = utils.list_data(self.app.client_manager.coast,
                               '/appgroups/' + args.appgroup +
                               '/environments',
                               'environments', args)
        fields = ('id', 'name', 'appgroup_id', 'status')
        result = (('ID', 'Name', 'Appgroup ID', 'Status'),
                  (utils.get_dict_properties(row, fields) for row in rows))
        return utils.project_column(args, rows, result)


class ShowEnvironment(command.ShowOne):
    """Show environment fields and current children."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('environment', metavar='ENVIRONMENT',
                            help='Environment name or ID.')
        parser.add_argument('--appgroup', metavar='APPGROUP',
                            help='App group name or ID.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        resource_id, data = scoped_environment(api, args)
        filters = {'environment_id': resource_id}
        data.update(utils.workload_children(api, filters))
        return utils.show(data)


class DeleteEnvironment(command.Command):
    """Submit environment deletion without waiting for completion."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('environment', metavar='ENVIRONMENT',
                            help='Environment name or ID.')
        parser.add_argument('--appgroup', metavar='APPGROUP',
                            help='App group name or ID.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        resource_id, _data = scoped_environment(api, args, load=False)
        utils.request(api, '/environments/' + resource_id, 'DELETE')


def scoped_environment(api, args, load=True):
    """Confirm environment ownership for both name and UUID references."""
    group = (utils.find_resource(api, args.appgroup, 'appgroup')
             if args.appgroup is not None else None)
    try:
        identity = utils.find_resource(api, args.environment, 'environment',
                                       appgroup_id=group)
    except exceptions.CommandError as error:
        if (group is not None and
                str(error) == utils.not_found(
                    'environment', args.environment)):
            raise exceptions.CommandError(
                "Environment '%s' not found in app group '%s'." % (
                    args.environment, args.appgroup)) from None
        raise
    if not load and group is None:
        return identity, None
    data = utils.resource_data(utils.request(
        api, '/environments/' + identity, 'GET'), 'environment')
    if group is not None and data.get('appgroup_id') != group:
        raise exceptions.CommandError(
            "Environment '%s' not found in app group '%s'." % (
                args.environment, args.appgroup))
    return identity, data
