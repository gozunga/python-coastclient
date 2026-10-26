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

"""Stack worker resize CLI wire contract."""
import json

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import stack
from coastclient.tests.unit import labels
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase6
from coastclient.tests.unit import test_upgrade


class StackResizeTest(base.BaseTestCase):
    execute = test_upgrade.UpgradeTest.execute
    register = test_upgrade.UpgradeTest.register
    assert_request = test_upgrade.UpgradeTest.assert_request
    assert_no_top_level_parent_lists = (
        test_upgrade.UpgradeTest.assert_no_top_level_parent_lists)
    STACK_ACTION = test_upgrade.UpgradeTest.STACK_ACTION

    def setUp(self):
        base.BaseTestCase.setUp(self)
        self.http = self.useFixture(test_commands.fixture.Fixture())
        self.register('GET', 'services', json={'services': []})
        self.register('GET', test_commands.ENVIRONMENTS_PATH,
                      json={'environments': []})
        self.addCleanup(self.assert_no_top_level_parent_lists)
        token = test_commands.ks_fixture.V3Token(project_id='project')
        token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region', url=test_commands.ROOT)
        auth_url = 'https://identity.example.com/v3'
        self.http.post(auth_url + '/auth/tokens', json=token,
                       headers={'X-Subject-Token': 'test-token'})
        self.session = test_commands.session.Session(
            auth=test_commands.v3.Token(
                auth_url=auth_url, token='input-token', project_id='project'))
        self.http.get(test_commands.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.28', 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.api = test_commands.client.Client(
            self.session, region_name='region')
        self.app = test_commands.SimpleNamespace(
            client_manager=test_commands.SimpleNamespace(coast=self.api),
            stdout=test_commands.io.StringIO())

        self.argv = [test_commands.STACK, '--flavor', 'larger-worker']
        self.body = {'resize_workers': {'flavor': 'larger-worker'}}

    def test_accepted_action_and_shell_output(self):
        result = {'id': test_commands.STACK, 'status': 'RESIZING'}
        route = self.register('POST', self.STACK_ACTION, status_code=202,
                              json={'stack': result})
        code, app = test_phase6.Phase6Test.run_shell(
            self, ['--os-coast-api-microversion', '1.28',
                   'coast', 'stack', 'resize', *self.argv, '-f', 'json'])
        self.assertEqual(0, code)
        self.assertEqual(labels.labelled(result),
                         json.loads(app.stdout.getvalue()))
        self.assertEqual(1, route.call_count)
        self.assert_request('POST', self.STACK_ACTION, self.body,
                            version='1.28')
        self.assertEqual('1.28', self.api.microversion)

    def test_default_microversion_supports_worker_resize(self):
        self.assertEqual('1.28', self.api.microversion)
        self.register('POST', self.STACK_ACTION, status_code=202,
                      json={'stack': {'id': test_commands.STACK}})
        self.execute(stack.ResizeStack, self.argv)
        self.assert_request('POST', self.STACK_ACTION, self.body,
                            version='1.28')

    def test_name_resolution_follows_pages(self):
        self.register('GET', 'stacks', json={'stacks': [], 'links': [{
            'rel': 'next', 'href': '?marker=next'}]})
        self.register('GET', 'stacks?marker=next', json={'stacks': [{
            'id': test_commands.STACK, 'name': 'demo'}]})
        self.register('POST', self.STACK_ACTION, status_code=202,
                      json={'stack': {'id': test_commands.STACK}})
        self.execute(stack.ResizeStack, ['demo', *self.argv[1:]])
        self.assert_request('POST', self.STACK_ACTION, self.body,
                            version='1.28')

    def test_resolution_errors_do_not_submit(self):
        for rows, message in (([], 'Stack'),
                              ([{'name': 'demo'}] * 2, 'More than one stack')):
            self.register('GET', 'stacks', json={'stacks': rows})
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      stack.ResizeStack,
                                      ['demo', *self.argv[1:]])
            self.assertIn(message, str(error))
        self.assertFalse(any(r.method == 'POST' and
                             r.url.endswith('/action')
                             for r in self.http.request_history))

    def test_errors_preserve_api_message_without_retry(self):
        for status, message in (
                (400, 'Every worker must move to a strictly larger flavor'),
                (404, 'Stack not found'),
                (409, 'Stack is not ACTIVE'),
                (409, 'A server is busy')):
            route = self.register('POST', self.STACK_ACTION,
                                  status_code=status, json={
                                      'faultcode': 'Client',
                                      'faultstring': message})
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      stack.ResizeStack, self.argv)
            expected = 'Coast API: %s (HTTP %s).' % (message, status)
            self.assertEqual(expected, str(error))
            self.assertEqual(1, route.call_count)
            self.assert_request('POST', self.STACK_ACTION, self.body,
                                version='1.28')

    def test_required_arguments_and_no_aliases(self):
        for argv in ([self.argv[0]], self.argv[1:],
                     [self.argv[0], '--flav', 'larger-worker'],
                     ['--stack', *self.argv], self.argv + ['--wait']):
            self.assertRaises(SystemExit, self.execute, stack.ResizeStack,
                              argv)

    def test_help_explains_scope(self):
        parser = stack.ResizeStack(self.app, None).get_parser('stack resize')
        help_text = parser.format_help()
        for text in ('every worker one at a time', 'larger flavors only',
                     'server resize', 'single server', '1.26'):
            self.assertIn(text, help_text)
