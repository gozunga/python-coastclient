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

"""Deployment commands using service-scoped public routes."""

from coastclient.osc.v1 import command
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils


def scoped(parser, item=False):
    parser.allow_abbrev = False
    parser.add_argument('--service', required=True, metavar='SERVICE',
                        help='App or service name or ID.')
    if item:
        parser.add_argument('deployment', type=utils.resource_id,
                            metavar='DEPLOYMENT_ID',
                            help='Deployment UUID.')
    return parser


def collection(api, args):
    return ('/services/' + utils.find_resource(api, args.service, 'service') +
            '/deployments')


def item_path(api, args):
    return collection(api, args) + '/' + args.deployment


class Deploy(command.ShowOne):
    """Create a deployment without waiting for completion."""

    def get_parser(self, prog_name):
        parser = service.service_parser(super().get_parser(prog_name))
        parser.add_argument('--source-ref',
                            help='Source reference to deploy; omit for '
                                 'the configured reference.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        values = {}
        if args.source_ref is not None:
            values['source_ref'] = args.source_ref
        data = utils.request(api, collection(api, args),
                             'POST', json={'deployment': values})
        return utils.show(utils.resource_data(data, 'deployment'))


# Fault text is a sentence, so it is not a default list column.
# Select it with -c fault. Cliff matches the header "Fault".
DEPLOYMENT_COLUMNS = (
    ('id', 'ID'),
    ('number', 'Number'),
    ('status', 'Status'),
    ('source_ref', 'Source Ref'),
    ('resolved_ref', 'Resolved Ref'),
    ('trigger', 'Trigger'),
)


def _wants_column(args, name):
    requested = getattr(args, 'columns', None) or []
    return any(column.lower().strip().replace(' ', '_') == name
               for column in requested)


class ListDeployment(command.Lister):
    """List deployments in an app or service.

    The Fault column is optional (-c fault). The failure text is wide.
    """

    admin_scope = 'list'

    def get_parser(self, prog_name):
        parser = utils.list_parser(
            scoped(super().get_parser(prog_name)), 'deployment')
        parser.add_argument('--long', action='store_true',
                            help='Include server, rollback and timing fields.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        rows = utils.list_data(api, collection(api, args),
                               'deployments', args)
        fields = list(DEPLOYMENT_COLUMNS)
        if args.long:
            fields.extend((key, utils.label(key)) for key in (
                'server_id', 'rollback_of', 'started_at', 'finished_at',
                'created_at', 'updated_at'))
        if _wants_column(args, 'fault'):
            fields.append(('fault', 'Fault'))
        headers = tuple(header for _key, header in fields)
        keys = tuple(key for key, _header in fields)
        result = (headers,
                  (utils.get_dict_properties(row, keys) for row in rows))
        return utils.project_column(args, rows, result)


class ShowDeployment(command.ShowOne):
    """Show public deployment fields, including the fault reason."""

    def get_parser(self, prog_name):
        return scoped(super().get_parser(prog_name), item=True)

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api, item_path(api, args),
                             'GET')
        # fault is public (service_controllers.DEP_FIELDS). spec,
        # image_digest, engine_ref, and log_ref stay hidden.
        public = ('id', 'project_id', 'service_id', 'number', 'source_ref',
                  'resolved_ref', 'server_id', 'trigger', 'rollback_of',
                  'status', 'fault', 'runtime_log', 'started_at',
                  'finished_at', 'created_at', 'updated_at', 'links')
        return utils.show({key: value
                           for key, value in data['deployment'].items()
                           if key in public})


class LogDeployment(command.ShowOne):
    """Read deployment log lines and completion state once."""

    def get_parser(self, prog_name):
        return scoped(super().get_parser(prog_name), item=True)

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api,
                             item_path(api, args) + '/log', 'GET')
        log = data['log']
        utils.log_notices(self.app, log)
        fields = ('deployment_id', 'complete', 'lines')
        values = utils.show({key: log[key] for key in fields})
        by_key = dict(zip(*values))
        return (('Deployment ID', 'Complete', 'Lines'),
                tuple(by_key[key] for key in fields))


class RollbackDeployment(command.ShowOne):
    """Create a new rollback deployment without waiting."""

    def get_parser(self, prog_name):
        return scoped(super().get_parser(prog_name), item=True)

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api,
                             item_path(api, args) + '/action', 'POST',
                             json={'rollback': {}})
        return utils.show(utils.resource_data(data, 'deployment'))


class CancelDeployment(command.ShowOne):
    """Cancel the latest in-flight deployment without waiting."""

    def get_parser(self, prog_name):
        return scoped(super().get_parser(prog_name), item=True)

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api,
                             item_path(api, args) + '/action', 'POST',
                             json={'cancel': {}})
        return utils.show(utils.resource_data(data, 'deployment'))
