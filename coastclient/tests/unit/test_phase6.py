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

"""Quota wire semantics and complete installed CLI acceptance."""
import io
import json
from types import SimpleNamespace

import fixtures
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from openstackclient import shell
from osc_lib import clientmanager
from osc_lib.command import command
from osc_lib import exceptions
from oslotest import base
import requests
from requests_mock.contrib import fixture

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import quota
from coastclient.osc.v1 import utils
from coastclient.tests.unit import labels
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase4
from coastclient.tests.unit import test_phase5
from coastclient.v1 import client

ROOT = 'https://app.example.com/'
BASE = ROOT + 'v1/'
PROJECT = '77777777-7777-4777-8777-777777777777'
FOREIGN = '88888888-8888-4888-8888-888888888888'
PATH = 'quotas/' + PROJECT
LIMITS = dict(zip(quota.LIMITS, (1, 2, 0, 1, 10, 20, 200)))
USAGE = {'in_use': dict.fromkeys(quota.LIMITS, 0),
         'reserved': dict.fromkeys(quota.LIMITS, 0)}
CASES = (
    (quota.ShowQuota, ['--project', PROJECT], 'GET', PATH, 'quota', None),
    (quota.SetQuota, ['--project', PROJECT, '--apps', '0'], 'PUT', PATH,
     'quota', {'quota': {'apps': 0}}),
)
AUTH = ['--os-auth-type', 'v3token', '--os-auth-url',
        'https://identity.example.com/v3', '--os-token', 'input-token',
        '--os-project-id', PROJECT, '--os-region-name', 'region']


class Phase6Test(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.input = io.StringIO('private-value')
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        self.http = self.useFixture(fixture.Fixture())
        token = ks_fixture.V3Token(project_id=PROJECT)
        token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region', url=ROOT)
        auth_url = 'https://identity.example.com/v3'
        self.http.post(auth_url + '/auth/tokens', json=token,
                       headers={'X-Subject-Token': 'test-token'})
        self.session = session.Session(auth=v3.Token(
            auth_url=auth_url, token='input-token', project_id=PROJECT))
        self.http.get(ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.1', 'links': [{'rel': 'self', 'href': BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.0')
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO())

    def execute(self, cls, argv, formatted=False):
        self.input.seek(0)
        cmd = cls(self.app, None)
        args = cmd.get_parser('test').parse_args(argv)
        return cmd.run(args) if formatted else cmd.take_action(args)

    def register(self, method, path, **kwargs):
        return self.http.register_uri(method, BASE + path, **kwargs)

    def assert_request(self, method, path, body=None, version='1.0'):
        req = self.http.last_request
        self.assertEqual(method, req.method)
        self.assertEqual(BASE + path, req.url)
        self.assertEqual('app-hosting ' + version,
                         req.headers['OpenStack-API-Version'])
        self.assertEqual('test-token', req.headers['X-Auth-Token'])
        if body is None:
            self.assertFalse(req.body)
        else:
            self.assertEqual(body, req.json())

    def test_show_default_explicit_and_json(self):
        for argv in ([], ['--project', PROJECT]):
            self.register('GET', PATH, json={'quota': LIMITS, 'usage': USAGE})
            self.app.stdout = io.StringIO()
            self.execute(quota.ShowQuota, [*argv, '-f', 'json'], True)
            expected = [{'Resource': utils.label(key), 'Limit': value,
                         'In use': 0, 'Reserved': 0}
                        for key, value in LIMITS.items()]
            self.assertEqual(expected,
                             json.loads(self.app.stdout.getvalue()))
            self.assert_request('GET', PATH)

    def test_keystone_undashed_project_id_is_not_rewritten(self):
        project = PROJECT.replace('-', '')
        path = 'quotas/' + project
        self.register('GET', path, json={'quota': LIMITS})
        self.execute(quota.ShowQuota, ['--project', project])
        self.assert_request('GET', path)
        self.register('PUT', path, json={'quota': LIMITS})
        self.execute(quota.SetQuota, ['--project', project, '--apps', '0'])
        self.assert_request('PUT', path, {'quota': {'apps': 0}})

    def test_show_does_not_invent_usage(self):
        self.register('GET', PATH, json={'quota': {'apps': 10}})
        columns, values = self.execute(quota.ShowQuota, ['--project', PROJECT])
        self.assertEqual(('Resource', 'Limit', 'In use', 'Reserved'),
                         columns)
        self.assertEqual([('Apps', 10, None, None)], list(values))

    def test_all_limits_preserve_null_zero_unlimited(self):
        for value in ('default', '0', '-1', '12'):
            argv = ['--project', PROJECT]
            for key in quota.LIMITS:
                argv.extend(['--' + key.replace('_', '-'), value])
            self.register('PUT', PATH, json={'quota': LIMITS})
            before = len(self.http.request_history)
            self.app.stdout = io.StringIO()
            columns, values = self.execute(quota.SetQuota, argv)
            expected = None if value == 'default' else int(value)
            self.assert_request('PUT', PATH, {
                'quota': dict.fromkeys(quota.LIMITS, expected)})
            self.assertEqual(labels.labelled(LIMITS),
                             dict(zip(columns, values)))
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_partial_update_and_async_empty_output(self):
        for status in (200, 202, 204):
            self.register('PUT', PATH, status_code=status,
                          **({'json': {'quota': {'apps': 0}}}
                             if status != 204 else {}))
            self.app.stdout = io.StringIO()
            before = len(self.http.request_history)
            if status == 204:
                self.assertRaises(exceptions.CommandError, self.execute,
                                  quota.SetQuota,
                                  ['--project', PROJECT, '--apps', '0'])
            else:
                self.assertEqual((('Apps',), (0,)), self.execute(
                    quota.SetQuota,
                    ['--project', PROJECT, '--apps', '0']))
            self.assert_request('PUT', PATH, {'quota': {'apps': 0}})
            self.assertEqual('', self.app.stdout.getvalue())
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_invalid_arguments_no_request(self):
        for cls, argv in (
                (quota.ShowQuota, ['--project', 'name']), (quota.SetQuota, []),
                (quota.SetQuota, ['--project', 'name', '--apps', '0']),
                (quota.ShowQuota, ['--project', PROJECT, '--limit', '1'])):
            before = len(self.http.request_history)
            self.assertRaises(SystemExit, self.execute, cls, argv)
            self.assertEqual(before, len(self.http.request_history))
        for value in ('-2', 'true', '1.5', 'null', 'NULL'):
            self.assertRaises(SystemExit, self.execute, quota.SetQuota,
                              ['--project', PROJECT, '--apps', value])
        self.assertRaises(exceptions.CommandError, self.execute,
                          quota.SetQuota, ['--project', PROJECT])

    def test_quota_project_scope_has_no_positional_alias(self):
        for cls in (quota.ShowQuota, quota.SetQuota,
                    catchup.QuotaDelete):
            parser = cls(self.app, None).get_parser('test')
            for argv in ([PROJECT], ['--project', PROJECT, PROJECT]):
                self.assertRaises(SystemExit, parser.parse_args, argv)
            if cls is not quota.ShowQuota:
                self.assertRaises(SystemExit, parser.parse_args, [])

    def test_unscoped_without_project_no_request(self):
        self.useFixture(fixtures.MonkeyPatch(
            'keystoneauth1.session.Session.get_project_id',
            lambda self: None))
        before = len(self.http.request_history)
        error = self.assertRaises(exceptions.CommandError, self.execute,
                                  quota.ShowQuota, [])
        self.assertEqual(
            'Select a project-scoped credential or supply --project.',
            str(error))
        self.assertEqual(before, len(self.http.request_history))

    def test_explicit_11_quota_supported(self):
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.1')
        self.register('GET', 'quotas/' + PROJECT, json={'quota': {}})
        self.execute(quota.ShowQuota, [])
        self.assertEqual('app-hosting 1.1',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])

    def test_foreign_project_policy_and_admin_update(self):
        path = 'quotas/' + FOREIGN
        for cls, argv, method in (
                (quota.ShowQuota, ['--project', FOREIGN], 'GET'),
                (quota.SetQuota, ['--project', FOREIGN, '--apps', '0'],
                 'PUT')):
            for status in (403, 404):
                self.register(method, path, status_code=status,
                              json={'faultstring': 'remote-secret'})
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls, argv)
                self.assertIn(
                    'not found' if status == 404 else 'HTTP ' + str(status),
                    str(error))
            self.register(method, path, json={'quota': LIMITS})
            self.execute(cls, argv)
            self.assertEqual(BASE + path, self.http.last_request.url)

    def test_connection_and_invalid_json_errors(self):
        for cls, argv, method, path, envelope, body in CASES:
            for error in (requests.exceptions.ConnectTimeout,
                          requests.exceptions.ConnectionError):
                self.register(method, path, exc=error)
                before = len(self.http.request_history)
                self.assertRaises(exceptions.CommandError,
                                  self.execute, cls, argv)
                self.assertEqual(1, len(self.http.request_history) - before)
            self.register(method, path, text='remote-secret')
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      cls, argv)
            self.assertNotIn('remote-secret', str(error))

    def run_shell(self, argv):
        # OSC caches plugin clients at class scope; each CLI process is fresh.
        cache = clientmanager.ClientManager.__dict__['coast']
        self.addCleanup(setattr, cache, '_handle', cache._handle)
        cache._handle = None
        app = shell.OpenStackShell()
        app.stdout = io.StringIO()
        app.stderr = io.StringIO()
        result = app.run([*AUTH, '--os-coast-api-microversion', '1.0',
                          *argv])
        return result, app

    def test_every_m5_command_through_installed_shell(self):
        # This is an offline CLI gate, not proof of live provisioning.
        cases = []
        for cls, argv, method, path, envelope in test_commands.CASES:
            body = None
            if cls is test_commands.stack.CreateStack:
                body = {'stack': {'name': 'demo', 'plan': 'control'}}
            elif cls is test_commands.server.AddServer:
                body = {'server': {'name': 'worker', 'kind': 'vm',
                                   'role': 'worker', 'plan': 'worker'}}
            elif method == 'POST':
                body = {envelope: {'name': 'demo'}}
            cases.append((cls, argv, method, path, envelope, body))
        cases += (list(test_phase4.CASES) + list(test_phase5.CASES) +
                  list(CASES))
        for cls, argv, method, path, envelope, body in cases:
            if cls.__name__ == 'Deploy':
                words = ['deploy']
            elif cls.__name__ == 'ShowLogs':
                words = ['logs']
            else:
                resource = cls.__module__.rsplit('.', 1)[-1]
                if resource == 'service' and cls.__name__.endswith('App'):
                    resource = 'app'
                verb = cls.__name__.removesuffix(resource.title()).lower()
                words = [resource, verb]
            for status in (200, 404):
                resource = {'id': PROJECT, 'status': 'ACTIVE'}
                response = {envelope: [resource] if envelope.endswith('s')
                            and envelope != 'logs' else resource}
                if envelope == 'env':
                    response = {'env': {}}
                if envelope == 'logs':
                    response = {'logs': {'lines': []}}
                if envelope == 'log':
                    response = {'log': {'deployment_id': PROJECT,
                                        'complete': False, 'lines': []}}
                self.register(method, path, status_code=status, json=(
                    response if status == 200 else
                    {'faultstring': 'remote-secret'}))
                if words in (['appgroup', 'show'], ['environment', 'show']):
                    self.register('GET', 'services', json={'services': []})
                    self.register('GET', test_commands.ENVIRONMENTS_PATH,
                                  json={'environments': []})
                self.input.seek(0)
                logger = self.useFixture(fixtures.FakeLogger(level=10))
                formatter = ['-f', 'json'] if issubclass(
                    cls, (command.ShowOne, command.Lister)) else []
                result, app = self.run_shell(
                    ['coast', *words, *argv, *formatter])
                self.assertEqual(status == 200, result == 0,
                                 ' '.join(words))
                self.assertNotIn('remote-secret', logger.output)
                self.assertNotIn('private-value', app.stdout.getvalue())
                if status == 200:
                    if words in (['appgroup', 'show'],
                                 ['environment', 'show']):
                        index = -3 if words[0] == 'appgroup' else -2
                        request = self.http.request_history[index]
                        self.assertEqual(BASE + path, request.url)
                        self.assertEqual(method, request.method)
                    else:
                        self.assert_request(
                            method, path, body,
                            '1.11' if cls is test_phase4.service.CreateApp
                            else '1.0')
                    if method == 'DELETE':
                        self.assertEqual('', app.stdout.getvalue())
                    else:
                        json.loads(app.stdout.getvalue())

    def test_shell_quota_json_and_11_supported(self):
        self.register('GET', PATH, json={'quota': LIMITS, 'usage': USAGE})
        result, app = self.run_shell(['coast', 'quota', 'show', '-f', 'json'])
        self.assertEqual(0, result)
        self.assertEqual([{'Resource': utils.label(key), 'Limit': value,
                          'In use': 0, 'Reserved': 0}
                         for key, value in LIMITS.items()],
                         json.loads(app.stdout.getvalue()))
        self.register('PUT', PATH, json={'quota': LIMITS})
        for words in (['show'], ['set', '--project', PROJECT, '--apps', '0']):
            before = len(self.http.request_history)
            result, app = self.run_shell([
                '--os-coast-api-microversion', '1.1', 'coast', 'quota',
                *words])
            self.assertEqual(0, result)
            self.assertTrue(any('/quotas/' in req.url for req in
                                self.http.request_history[before:]))


for _cls, _argv, _method, _path, _envelope, _body in CASES:
    for _status in (400, 401, 403, 404, 406, 409, 500, 503):
        setattr(Phase6Test, 'test_%s_http_%s' % (_cls.__name__, _status),
                test_commands.error_test(
                    _cls, _argv, _method, _path, _status))
