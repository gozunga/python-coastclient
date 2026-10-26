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

"""Server resize wire contract and public output."""
import io
import json
from types import SimpleNamespace

from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from osc_lib import exceptions
from oslotest import base
from requests_mock.contrib import fixture

from coastclient.osc.v1 import server
from coastclient.tests.unit import labels
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase6
from coastclient.v1 import client

PATH = test_commands.SERVER_PATH + '/action'
ARGS = [test_commands.SERVER, '--stack', test_commands.STACK, '--flavor',
        'larger-worker']


class ResizeTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.http = self.useFixture(fixture.Fixture())
        self.register('GET', 'services', json={'services': []})
        self.addCleanup(self.assert_no_top_level_parent_lists)
        token = ks_fixture.V3Token(project_id='project')
        token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region', url=test_commands.ROOT)
        auth_url = 'https://identity.example.com/v3'
        self.http.post(auth_url + '/auth/tokens', json=token,
                       headers={'X-Subject-Token': 'test-token'})
        self.session = session.Session(auth=v3.Token(
            auth_url=auth_url, token='input-token', project_id='project'))
        self.http.get(test_commands.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.1', 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.0')
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO())

    execute = test_commands.CommandTest.execute
    register = test_commands.CommandTest.register
    assert_request = test_commands.CommandTest.assert_request
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)

    def test_request_and_transitional_output(self):
        result = {'id': test_commands.SERVER, 'status': 'FAILOVER',
                  'task_state': 'FAILING_OVER', 'flavor': 'old-worker'}
        self.register('POST', PATH, status_code=202, json={'server': result})
        for flavor in ('larger-worker', test_commands.SERVER):
            self.app.stdout = io.StringIO()
            self.execute(server.ResizeServer,
                         ARGS[:-1] + [flavor, '-f', 'json'], True)
            self.assert_request('POST', PATH, {'resize': {'flavor': flavor}},
                                version='1.26')
            self.assertEqual(labels.labelled(result),
                             json.loads(self.app.stdout.getvalue()))
            self.assertEqual('1.0', self.api.microversion)

    def test_api_errors_are_plain_and_not_retried(self):
        for status, message in (
                (400, 'Choose a strictly larger flavor; to go smaller, add a '
                 'new, smaller worker and move your applications instead.'),
                (409, 'Stack is busy.'),
                (406, 'Microversion 1.26 is not supported.')):
            route = self.register('POST', PATH, status_code=status, json={
                'faultcode': 'Client', 'faultstring': message,
                'debuginfo': None})
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      server.ResizeServer, ARGS)
            self.assertEqual('Coast API: %s (HTTP %s).' % (message, status),
                             str(error))
            self.assertEqual(1, route.call_count)
            self.assert_request('POST', PATH,
                                {'resize': {'flavor': 'larger-worker'}},
                                version='1.26')

    def test_required_arguments_and_uuid_scope(self):
        for argv in (ARGS[:3], ARGS[:1] + ARGS[3:], ARGS[1:],
                     ARGS + ['--wait']):
            self.assertRaises(SystemExit, self.execute, server.ResizeServer,
                              argv)

    def test_resize_hint_rejects_other_routes_and_versions(self):
        before = len(self.http.request_history)
        for path, method, version in (
                ('/stacks/id/servers/id/action', 'GET', '1.26'),
                ('/stacks/id/servers/id', 'POST', '1.26'),
                ('/services/id/action', 'POST', '1.26'),
                ('/stacks/id/servers/id/action', 'POST', '1.25')):
            self.assertRaises(ValueError, self.api.request, path, method,
                              server_resize_microversion=version)
        self.assertEqual(before, len(self.http.request_history))

    def test_negotiated_126(self):
        self.http.get(test_commands.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.26', 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region')
        self.register('POST', PATH, status_code=202,
                      json={'server': {'status': 'FAILOVER'}})
        self.execute(server.ResizeServer, ARGS)
        self.assert_request('POST', PATH,
                            {'resize': {'flavor': 'larger-worker'}},
                            version='1.26')

    def test_installed_shell_command(self):
        result = {'id': test_commands.SERVER, 'status': 'FAILOVER'}
        self.register('POST', PATH, status_code=202, json={'server': result})
        status, app = test_phase6.Phase6Test.run_shell(self, [
            'coast', 'server', 'resize', *ARGS, '-f', 'json'])
        self.assertEqual(0, status)
        self.assertEqual(labels.labelled(result),
                         json.loads(app.stdout.getvalue()))
        self.assert_request('POST', PATH,
                            {'resize': {'flavor': 'larger-worker'}},
                            version='1.26')
