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

"""One-shot service logs; no streaming or polling."""

from coastclient.osc.v1 import command
from coastclient.osc.v1 import deployment
from coastclient.osc.v1 import utils


class ShowLogs(command.Lister):
    """Read app or service logs once, with optional server-side filters."""

    def get_parser(self, prog_name):
        parser = deployment.scoped(super().get_parser(prog_name))
        parser.add_argument('--since',
                            help='ISO 8601 UTC timestamp; omit for no '
                                 'time filter.')
        parser.add_argument('--tail', type=utils.nonnegative,
                            help='Recent line count (integer >= 0); live API '
                                 'default: 200. Zero returns no lines.')
        return parser

    def take_action(self, args):
        params = {key: getattr(args, key) for key in ('since', 'tail')
                  if getattr(args, key) is not None}
        data = utils.request(self.app.client_manager.coast,
                             '/services/' + utils.find_resource(
                                 self.app.client_manager.coast,
                                 args.service, 'service') + '/logs', 'GET',
                             params=params)
        utils.log_notices(self.app, data['logs'])
        return (('Timestamp', 'Stream', 'Line'),
                (utils.get_dict_properties(row, ('ts', 'stream', 'line'))
                 for row in data['logs']['lines']))
