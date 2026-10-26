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

"""Public stack actions."""
from urllib import parse


class StackManager:
    """Submit asynchronous actions at the selected microversion."""

    def __init__(self, client):
        self.client = client

    def _action(self, stack_id, action, values):
        path = '/stacks/%s/action' % parse.quote(str(stack_id), safe='')
        response = self.client.request(path, 'POST', json={action: values})
        return response.json()['stack']

    def upgrade(self, stack_id, image_id):
        """Upgrade to a published App engine server image UUID."""
        return self._action(stack_id, 'upgrade', {'image_id': image_id})

    def upgrade_rollback(self, stack_id):
        """Rebuild with the previous App engine image."""
        return self._action(stack_id, 'upgrade_rollback', {})
