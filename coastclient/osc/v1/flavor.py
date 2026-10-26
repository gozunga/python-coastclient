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

"""Allowed VM flavors from the public Coast catalog."""
from keystoneauth1 import exceptions as ks_exc
from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


class ListFlavor(command.Lister):
    """List allowed VM sizes and their plans and server roles."""

    route = '/flavors'
    method = 'GET'

    def take_action(self, args):
        def request():
            try:
                return self.app.client_manager.coast.request(
                    self.route, self.method, flavors_microversion='1.27')
            except (ks_exc.NotFound, ks_exc.NotAcceptable,
                    ks_exc.DiscoveryFailure):
                raise exceptions.CommandError(
                    'Flavor list needs Coast API 1.27 or later.') from None

        response = utils.api_call(request)
        try:
            rows = response.json()['flavors']
        except ValueError:
            raise exceptions.CommandError(
                'Invalid Coast API response.') from None
        fields = ('id', 'name', 'plan', 'vcpus', 'ram', 'disk', 'roles')
        return (('ID', 'Name', 'Plan', 'vCPUs', 'RAM (MiB)', 'Disk (GiB)',
                 'Roles'),
                (utils.get_dict_properties(
                    row, fields,
                    formatters={'roles': utils.RolesColumn})
                 for row in rows))
