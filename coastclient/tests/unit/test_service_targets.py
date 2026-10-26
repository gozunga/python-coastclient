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

"""Parsing and HTTP resolution for every app or service target."""
import io
from types import SimpleNamespace

import fixtures
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from osc_lib import exceptions
from oslotest import base
from requests_mock.contrib import fixture

from coastclient.osc.v1 import service
from coastclient.tests.unit import test_phase4
from coastclient.tests.unit import test_phase5
from coastclient.v1 import client

ROOT = test_phase4.ROOT
BASE = test_phase4.BASE
SERVICE = test_phase4.SERVICE
OTHER = test_phase4.PARENT
CASES = tuple(case for case in test_phase4.CASES
              if case[0] not in (service.CreateApp, service.ListApp,
                                 service.ListService)) + test_phase5.CASES
CASES += tuple((alias, argv, method, path, envelope, body)
               for original, alias in (
                   (service.ShowService, service.ShowApp),
                   (service.DeleteService, service.DeleteApp),
                   (service.StartService, service.StartApp),
                   (service.StopService, service.StopApp),
                   (service.RestartService, service.RestartApp))
               for cls, argv, method, path, envelope, body in CASES
               if cls is original)


class ServiceTargetTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.input = io.StringIO('private-value')
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        self.http = self.useFixture(fixture.Fixture())
        token = ks_fixture.V3Token(project_id='project')
        token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region', url=ROOT)
        auth_url = 'https://identity.example.com/v3'
        self.http.post(auth_url + '/auth/tokens', json=token,
                       headers={'X-Subject-Token': 'test-token'})
        self.session = session.Session(auth=v3.Token(
            auth_url=auth_url, token='input-token', project_id='project'))
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


def target_test(cls, argv, method, path, envelope, body):
    def test(self):
        parser = cls(self.app, None).get_parser('test')
        for target in ('web', SERVICE, SERVICE.replace('-', '')):
            options = [target if arg == SERVICE else arg for arg in argv]
            self.assertEqual(target, parser.parse_args(options).service)
        self.assertIn('APP' if 'App' in cls.__name__ else 'SERVICE',
                      parser.format_help())
        self.assertIn('App or service name or ID.', parser.format_help())
        self.assertNotIn('SERVICE_ID', parser.format_help())

        response = {}
        if envelope in ('deployments', 'domains'):
            response = []
        elif envelope == 'logs':
            response = {'lines': []}
        elif envelope == 'log':
            response = {'deployment_id': OTHER, 'complete': True, 'lines': []}
        self.register(method, path, json={envelope: response})
        if cls is service.ShowApp:
            self.register('GET', path + '/webhooks', status_code=403)
        for kind in ('app', 'service'):
            self.register('GET', 'services', json={'services': [
                {'id': OTHER, 'name': 'web-extra', 'kind': kind}],
                'links': [{'rel': 'next', 'href': '?marker=' + OTHER}]})
            self.register('GET', 'services?marker=' + OTHER,
                          complete_qs=True, json={'services': [
                              {'id': SERVICE, 'name': 'web', 'kind': kind}]})
            before = len(self.http.request_history)
            self.execute(cls, ['web' if arg == SERVICE else arg
                               for arg in argv])
            self.assert_request(method, path, body)
            self.assertEqual(3 + (cls is service.ShowApp),
                             len(self.http.request_history) - before)
            self.assertEqual({}, self.http.request_history[before].qs)

        before = len(self.http.request_history)
        self.execute(cls, argv)
        self.assert_request(method, path, body)
        self.assertEqual(1 + (cls is service.ShowApp),
                         len(self.http.request_history) - before)
    return test


def lookup_error_test(cls, argv, method, path, envelope, body):
    def test(self):
        options = ['web' if arg == SERVICE else arg for arg in argv]
        for rows, message in (
                ([], "App or service 'web' not found."),
                ([{'id': SERVICE, 'name': 'web', 'kind': 'app'},
                  {'id': OTHER, 'name': 'web', 'kind': 'service'}],
                 'More than one app or service')):
            self.register('GET', 'services', json={'services': rows},
                          complete_qs=True)
            before = len(self.http.request_history)
            error = self.assertRaises(exceptions.CommandError,
                                      self.execute, cls, options)
            self.assertIn(message, str(error))
            self.assertEqual(1, len(self.http.request_history) - before)
        self.register('GET', 'services', status_code=403,
                      json={'faultstring': 'remote-secret'}, complete_qs=True)
        before = len(self.http.request_history)
        error = self.assertRaises(exceptions.CommandError,
                                  self.execute, cls, options)
        self.assertIn('HTTP 403', str(error))
        self.assertNotIn('remote-secret', str(error))
        self.assertEqual(1, len(self.http.request_history) - before)
    return test


for _case in CASES:
    setattr(ServiceTargetTest, 'test_%s_target' % _case[0].__name__,
            target_test(*_case))
    setattr(ServiceTargetTest, 'test_%s_lookup_errors' % _case[0].__name__,
            lookup_error_test(*_case))
