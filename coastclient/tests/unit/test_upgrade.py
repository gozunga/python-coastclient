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

"""Stack upgrade manager and OSC wire contracts."""
import json

from keystoneauth1 import exceptions as ks_exc
from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import stack
from coastclient.tests.unit import labels
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase6


class UpgradeTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
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
            'max_version': '1.1', 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.api = test_commands.client.Client(
            self.session, region_name='region', microversion='1.0')
        self.app = test_commands.SimpleNamespace(
            client_manager=test_commands.SimpleNamespace(coast=self.api),
            stdout=test_commands.io.StringIO())

    execute = test_commands.CommandTest.execute
    register = test_commands.CommandTest.register
    assert_request = test_commands.CommandTest.assert_request
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)

    def test_upgrade_commands_and_managers(self):
        path = self.STACK_ACTION
        for cls, method, action, argv, values, status in self.cases():
            result = {'id': test_commands.STACK, 'status': status}
            self.register('POST', path, status_code=202,
                          json={'stack': result})
            self.assertEqual(result, method(*values))
            self.assert_request('POST', path, action)
            self.execute(cls, argv + ['-f', 'json'], True)
            self.assertEqual(labels.labelled(result),
                             json.loads(self.app.stdout.getvalue()))
            self.app.stdout.seek(0)
            self.app.stdout.truncate()
            self.assert_request('POST', path, action)

    STACK_ACTION = test_commands.STACK_PATH + '/action'

    def cases(self):
        sid = test_commands.STACK
        image = test_commands.SERVER
        return (
            (stack.UpgradeStack, self.api.stacks.upgrade,
             {'upgrade': {'image_id': image}}, [sid, '--image', image],
             (sid, image), 'UPGRADING'),
            (stack.RollbackStackUpgrade, self.api.stacks.upgrade_rollback,
             {'upgrade_rollback': {}}, [sid], (sid,), 'ROLLING_BACK'))

    def test_action_errors(self):
        for cls, method, action, argv, values, status in self.cases():
            for code, message in ((400, 'Image is not eligible'),
                                  (404, 'Stack not found'),
                                  (409, 'Stack is busy')):
                route = self.register('POST', self.STACK_ACTION,
                                      status_code=code, json={
                                          'faultcode': 'Client',
                                          'faultstring': message})
                self.assertRaises(ks_exc.HttpError, method, *values)
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls, argv)
                expected = 'Coast API: %s (HTTP %s).' % (message, code)
                self.assertEqual(expected,
                                 str(error))
                self.assertEqual(2, route.call_count)
                self.assert_request('POST', self.STACK_ACTION, action)

    def test_stack_name_resolution_and_shell(self):
        for cls, method, action, argv, values, status in self.cases():
            self.register('GET', 'stacks', json={'stacks': [], 'links': [{
                'rel': 'next', 'href': '?marker=next'}]})
            self.register('GET', 'stacks?marker=next', json={'stacks': [{
                'id': test_commands.STACK, 'name': 'demo'}]})
            result = {'id': test_commands.STACK, 'status': status}
            self.register('POST', self.STACK_ACTION, status_code=202,
                          json={'stack': result})
            command = ['coast', 'stack', 'upgrade']
            if cls is stack.RollbackStackUpgrade:
                command += ['rollback']
            code, app = test_phase6.Phase6Test.run_shell(
                self, command + ['demo'] + argv[1:] + ['-f', 'json'])
            self.assertEqual(0, code)
            self.assertEqual(labels.labelled(result),
                             json.loads(app.stdout.getvalue()))
            self.assert_request('POST', self.STACK_ACTION, action)

    def test_missing_and_ambiguous_names(self):
        for cls, method, action, argv, values, status in self.cases():
            for rows, message in (([], 'Stack'),
                                  ([{'name': 'demo'}] * 2,
                                   'More than one stack')):
                self.register('GET', 'stacks', json={'stacks': rows})
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls,
                                          ['demo'] + argv[1:])
                self.assertIn(message, str(error))
            self.assertRaises(SystemExit, self.execute, cls, argv[1:])

    def test_legacy_upgrade_options_are_rejected(self):
        for cls, method, action, argv, values, status in self.cases():
            options = [['--stack', test_commands.STACK]]
            if cls is stack.UpgradeStack:
                options.append(['--image-id', test_commands.SERVER])
            for option in options:
                with self.subTest(command=cls.__name__, option=option):
                    before = len(self.http.request_history)
                    self.assertRaises(SystemExit, self.execute, cls,
                                      argv + option)
                    self.assertEqual(before, len(self.http.request_history))

    def test_image_uuid_is_required(self):
        for argv in ([test_commands.STACK],
                     [test_commands.STACK, '--image', 'image-name']):
            self.assertRaises(SystemExit, self.execute,
                              stack.UpgradeStack, argv)

    def test_selected_microversion_is_preserved(self):
        self.api.microversion = '1.26'
        for cls, method, action, argv, values, status in self.cases():
            self.register('POST', self.STACK_ACTION, status_code=202,
                          json={'stack': {'status': status}})
            self.execute(cls, argv)
            self.assert_request('POST', self.STACK_ACTION, action,
                                version='1.26')
            self.assertEqual('1.26', self.api.microversion)
        self.assertFalse(any(request.url.split('?')[0] ==
                             test_commands.BASE + 'stacks'
                             for request in self.http.request_history))
