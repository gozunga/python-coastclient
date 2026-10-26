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

"""Quota commands for the existing microversion 1.0 API."""
import argparse

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils

LIMITS = ('environments', 'servers', 'baremetal_servers', 'build_servers',
          'apps', 'domains', 'volume_gb', 'storage_gb')


def limit(value):
    if value == 'default':
        return None
    try:
        number = int(value)
        if number >= -1:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        "Use -1 for unlimited, 0 or more, or 'default'.")


def project_id(value):
    # Keystone commonly returns undashed UUIDs; preserve the policy identity.
    utils.resource_id(value)
    return value


def quota_api(app):
    api = app.client_manager.coast
    return api


class ShowQuota(command.Lister):
    """Show effective limits and returned usage for a project."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('--project', type=project_id,
                            metavar='PROJECT',
                            help='Project UUID; omit to use the '
                                 'authenticated project.')
        return parser

    def take_action(self, args):
        api = quota_api(self.app)
        project = args.project or api.adapter.session.get_project_id()
        if not project:
            raise exceptions.CommandError(
                'Select a project-scoped credential or supply --project.')
        data = utils.request(api, '/quotas/' + project, 'GET')
        usage = data.get('usage', {})
        return (('Resource', 'Limit', 'In use', 'Reserved'),
                ((utils.label(key), value, usage.get('in_use', {}).get(key),
                  usage.get('reserved', {}).get(key))
                 for key, value in data['quota'].items()))


class SetQuota(command.ShowOne):
    """Update selected project limits; Coast enforces admin policy."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        parser.add_argument('--project', required=True, type=project_id,
                            metavar='PROJECT',
                            help='Project UUID.')
        for key in LIMITS:
            parser.add_argument('--' + key.replace('_', '-'), type=limit,
                                default=argparse.SUPPRESS,
                                help=('Capacity in GiB'
                                      if key.endswith('_gb') else
                                      'Resource count') +
                                '; use 0 or more, -1 for unlimited, '
                                'or default for the configured default.')
        return parser

    def take_action(self, args):
        api = quota_api(self.app)
        values = {key: getattr(args, key) for key in LIMITS
                  if hasattr(args, key)}
        if not values:
            raise exceptions.CommandError('Supply at least one quota limit.')
        data = utils.request(api, '/quotas/' + args.project, 'PUT',
                             json={'quota': values})
        return utils.show(utils.resource_data(data, 'quota'))
