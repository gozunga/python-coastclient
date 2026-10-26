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

"""Phase-5 wire, output, validation and failure tests."""
import io
import json
from types import SimpleNamespace
from unittest import mock

import fixtures
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from openstackclient import shell
from osc_lib import exceptions
from oslotest import base
import requests
from requests_mock.contrib import fixture

from coastclient.osc.v1 import domain
from coastclient.osc.v1 import env
from coastclient.osc.v1 import utils
from coastclient.tests.unit import labels
from coastclient.tests.unit.test_commands import error_test
from coastclient.v1 import client

ROOT = 'https://app.example.com/'
BASE = ROOT + 'v1/'
SERVICE = '11111111-1111-4111-8111-111111111111'
PARENT = '33333333-3333-4333-8333-333333333333'
PATH = 'services/' + SERVICE
SCOPED = ['--service', SERVICE]
CASES = (
    (env.SetEnv, ['KEY', *SCOPED], 'PATCH', PATH + '/env',
     'env', {'env': {'KEY': {'value': 'private-value'}}}),
    (env.UnsetEnv, ['KEY', *SCOPED], 'PATCH', PATH + '/env',
     'env', {'env': {'KEY': None}}),
    (env.ListEnv, SCOPED, 'GET', PATH + '/env', 'env', None),
    (domain.AddDomain, ['www.example.com', *SCOPED], 'POST', PATH + '/domains',
     'domain', {'domain': {'fqdn': 'www.example.com'}}),
    (domain.ListDomain, SCOPED, 'GET', PATH + '/domains', 'domains', None),
    (domain.VerifyDomain, [PARENT, *SCOPED], 'POST',
     PATH + '/domains/' + PARENT + '/action', 'domain', {'verify': {}}),
    (domain.RemoveDomain, [PARENT, *SCOPED], 'DELETE',
     PATH + '/domains/' + PARENT, 'domain', None),
)


class Phase5Test(base.BaseTestCase):
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

    def test_wire_output_both_versions(self):
        for version in ('1.0', '1.1'):
            self.app.client_manager.coast = client.Client(
                self.session, region_name='region', microversion=version)
            for cls, argv, method, path, envelope, body in CASES:
                resource = {'id': PARENT, 'fqdn': 'www.example.com',
                            'status': 'PENDING_VERIFICATION',
                            'dns_records': [{'type': 'TXT', 'value': 'proof'}]}
                response = resource
                if envelope == 'env':
                    response = {
                        'KEY': {'value': 'private-value', 'secret': False,
                                'version': 3},
                        'SECRET': {'value': None, 'secret': True,
                                   'version': 1}}
                elif envelope == 'domains':
                    response = [resource]
                self.register(method, path, status_code=(
                    200 if method in ('GET', 'PATCH') else 202),
                    json={envelope: response})
                self.app.stdout = io.StringIO()
                before = len(self.http.request_history)
                self.execute(cls, argv + (
                    ['-f', 'json'] if envelope != 'env' and method != 'DELETE'
                    or method == 'GET' else []), True)
                if method == 'DELETE':
                    self.assertEqual('', self.app.stdout.getvalue())
                    self.assert_request(method, path, body, version)
                    continue
                output = json.loads(self.app.stdout.getvalue())
                self.assertNotIn('private-value',
                                 self.app.stdout.getvalue())
                if envelope == 'env':
                    expected = env.masked(response)
                    if method == 'GET':
                        expected = [
                            {'Key': 'KEY', 'Value': '********',
                             'Secret': False, 'Version': 3},
                            {'Key': 'SECRET', 'Value': '********',
                             'Secret': True, 'Version': 1}]
                    self.assertEqual(expected, output)
                elif method == 'GET':
                    self.assertEqual([{'ID': PARENT,
                                       'FQDN': 'www.example.com',
                                       'Status': 'PENDING_VERIFICATION',
                                       'TLS': '', 'Primary': ''}], output)
                else:
                    self.assertEqual(labels.labelled(response), output)
                self.assert_request(method, path, body, version)
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_redirected_input_trims_one_newline_and_terminal_hidden(self):
        self.input = io.StringIO('spaces = value\n')
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        self.register('PATCH', PATH + '/env', json={'env': {}})
        self.execute(env.SetEnv, ['KEY', *SCOPED])
        self.assertTrue(self.http.last_request.json() ==
                        {'env': {'KEY': {'value': 'spaces = value'}}})
        self.useFixture(fixtures.MonkeyPatch(
            'sys.stdin.isatty', lambda: True))
        with mock.patch('coastclient.osc.v1.env.getpass.getpass',
                        return_value='hidden'):
            self.execute(env.SetEnv, ['KEY', *SCOPED])
        self.assertTrue(self.http.last_request.json()['env']['KEY']['value']
                        == 'hidden')

    def test_debug_shell_does_not_echo_env_value(self):
        logger = self.useFixture(fixtures.FakeLogger(level=10))
        self.register('PATCH', PATH + '/env', json={'env': {
            'KEY': {'value': 'private-value', 'secret': False, 'version': 1}}})
        app = shell.OpenStackShell()
        app.stdout = io.StringIO()
        app.stderr = io.StringIO()
        result = app.run([
            '--debug', '--os-auth-type', 'v3token', '--os-auth-url',
            'https://identity.example.com/v3', '--os-token', 'input-token',
            '--os-project-id', 'project', '--os-region-name', 'region',
            'coast', 'env', 'set', 'KEY', *SCOPED])
        self.assertEqual(0, result)
        for output in (logger.output, app.stdout.getvalue(),
                       app.stderr.getvalue()):
            self.assertNotIn('private-value', output)

    def test_debug_redacts_pair_values_in_parsed_arguments(self):
        for cls in ('set', 'replace'):
            logger = self.useFixture(fixtures.FakeLogger(level=10))
            self.register('PATCH' if cls == 'set' else 'PUT',
                          PATH + '/env', json={'env': {}})
            app = shell.OpenStackShell()
            app.stdout = io.StringIO()
            app.stderr = io.StringIO()
            result = app.run([
                '--debug', '--os-auth-type', 'v3token', '--os-auth-url',
                'https://identity.example.com/v3', '--os-token',
                'input-token', '--os-project-id', 'project',
                '--os-region-name', 'region',
                'coast', 'env', cls, 'KEY=pair-value', *SCOPED])
            self.assertEqual(0, result)
            # osc_lib echoes the raw command line before plugins load
            # ("START with options"); the help says so. Everything the
            # client logs afterwards, including parsed arguments, is clean.
            lines = [line for line in logger.output.splitlines()
                     if not line.startswith('START with options')]
            self.assertIn("'KEY=<redacted>'", logger.output)
            for output in ('\n'.join(lines), app.stdout.getvalue(),
                           app.stderr.getvalue()):
                self.assertNotIn('pair-value', output)

    def test_pairs_after_options_and_parser_errors_redacted(self):
        for cls, method in ((env.SetEnv, 'PATCH'), (env.EnvReplace, 'PUT')):
            self.register(method, PATH + '/env', json={'env': {}})
            self.execute(cls, ['A=one', '--secret', 'B=two', *SCOPED])
            self.assertEqual({'env': {
                'A': {'value': 'one', 'secret': True},
                'B': {'value': 'two', 'secret': True}}},
                self.http.last_request.json())
            parser = cls(self.app, None).get_parser('coast env')
            stderr = io.StringIO()
            with mock.patch('sys.stderr', stderr):
                self.assertRaises(SystemExit, parser.parse_args, [
                    'A=pair-value', '--bogus', 'B=other value', *SCOPED])
            self.assertNotIn('pair-value', stderr.getvalue())
            self.assertNotIn('other value', stderr.getvalue())

    def test_boolean_flags_and_empty_value(self):
        self.input.truncate(0)
        for flag in (True, False):
            self.register('PATCH', PATH + '/env', json={'env': {}})
            self.execute(
                env.SetEnv, ['KEY=', *SCOPED,
                             '--secret' if flag else '--no-secret',
                             '--build-time' if flag else '--no-build-time'])
            self.assertEqual({'env': {'KEY': {
                'value': '', 'secret': flag, 'build_time': flag}}},
                self.http.last_request.json())
            self.register('POST', PATH + '/domains', status_code=201,
                          json={'domain': {'dns_records': []}})
            self.execute(
                domain.AddDomain, ['www.example.com', *SCOPED,
                                   '--auto-dns' if flag else '--no-auto-dns'])
            self.assertEqual({'domain': {'fqdn': 'www.example.com',
                                         'auto_dns': flag}},
                             self.http.last_request.json())

    def test_invalid_arguments_no_request(self):
        for cls, argv, method, path, envelope, body in CASES:
            for bad in ([], [*argv, '--wait']):
                before = len(self.http.request_history)
                self.assertRaises(SystemExit, self.execute, cls, bad)
                self.assertEqual(before, len(self.http.request_history))
        for cls, argv in (
                (env.SetEnv, ['bad-key', *SCOPED]),
                (env.UnsetEnv, ['1KEY', *SCOPED])):
            self.assertRaises(SystemExit, self.execute, cls, argv)

    def test_domain_commands_accept_the_fqdn(self):
        self.register('GET', PATH + '/domains', json={'domains': [
            {'id': PARENT, 'fqdn': 'bleh.com'}]})
        self.register('POST', PATH + '/domains/' + PARENT + '/action',
                      status_code=202, json={'domain': {'id': PARENT}})
        self.execute(domain.VerifyDomain, ['Bleh.com', *SCOPED])
        self.assertTrue(self.http.last_request.path.endswith(
            '/' + PATH + '/domains/' + PARENT + '/action'))
        self.assertRaises(exceptions.CommandError, self.execute,
                          domain.RemoveDomain, ['other.com', *SCOPED])

    def test_empty_and_connection_errors(self):
        for cls, argv, method, path, envelope, body in CASES:
            if method != 'GET':
                self.register(method, path, status_code=204)
                self.app.stdout = io.StringIO()
                if envelope in ('domain', 'env') and method != 'DELETE':
                    error = self.assertRaises(exceptions.CommandError,
                                              self.execute, cls, argv)
                    self.assertEqual('Invalid Coast API response.', str(error))
                else:
                    self.execute(cls, argv)
                self.assertEqual('', self.app.stdout.getvalue())
            for error in (requests.exceptions.ConnectTimeout,
                          requests.exceptions.ConnectionError):
                self.register(method, path, exc=error)
                before = len(self.http.request_history)
                self.assertRaises(exceptions.CommandError,
                                  self.execute, cls, argv)
                self.assertEqual(1, len(self.http.request_history) - before)
            self.register(method, path, text='private-value')
            error = self.assertRaises(exceptions.CommandError,
                                      self.execute, cls, argv)
            self.assertNotIn('private-value', str(error))

    def test_transport_logs_masked(self):
        logger = self.useFixture(fixtures.FakeLogger(
            name='keystoneauth.session', level=10))
        self.register('PATCH', PATH + '/env', json={'env': {
            'KEY': {'value': 'private-value', 'secret': False, 'version': 1}}})
        self.execute(env.SetEnv, ['KEY', *SCOPED])
        self.assertNotIn('private-value', logger.output)

    def test_pagination_and_empty_lists(self):
        path = PATH + '/domains'
        self.register('GET', path, json={'domains': [
            {'id': PARENT, 'fqdn': 'www.example.com', 'status': 'VERIFIED'}],
            'links': [{'rel': 'next', 'href': BASE + path +
                       '?marker=' + PARENT}]})
        self.register('GET', path + '?marker=' + PARENT, complete_qs=True,
                      json={'domains': []})
        columns, rows = self.execute(domain.ListDomain,
                                     [*SCOPED, '--limit', '1'])
        self.assertEqual(('ID', 'Status', 'FQDN', 'TLS', 'Primary'), columns)
        self.assertEqual([(PARENT, 'VERIFIED', 'www.example.com', '', '')],
                         list(rows))
        for cls, route, envelope, response in (
                (domain.ListDomain, path, 'domains', []),
                (env.ListEnv, PATH + '/env', 'env', {})):
            self.register('GET', route, json={envelope: response})
            self.app.stdout = io.StringIO()
            self.execute(cls, [*SCOPED, '-f', 'json'], True)
            self.assertEqual([], json.loads(self.app.stdout.getvalue()))

    def test_shell_commands(self):
        auth = ['--os-auth-type', 'v3token', '--os-auth-url',
                'https://identity.example.com/v3', '--os-token', 'input-token',
                '--os-project-id', 'project', '--os-region-name', 'region']
        for cls, argv, method, path, envelope, body in CASES:
            resource = 'env' if envelope == 'env' else 'domain'
            verb = cls.__name__.removesuffix(resource.title()).lower()
            for status in (200, 404):
                logger = self.useFixture(fixtures.FakeLogger(
                    name='run', level=10))
                self.register(method, path, status_code=status, json=(
                    {envelope: [] if envelope == 'domains' else {}}
                    if status == 200 else {'faultstring': 'remote-secret'}))
                app = shell.OpenStackShell()
                app.stdout = io.StringIO()
                app.stderr = io.StringIO()
                result = app.run([*auth, 'coast', resource, verb, *argv])
                self.assertEqual(status == 200, result == 0)
                self.assertNotIn('remote-secret', logger.output)

    def test_domain_list_and_show_print_tls(self):
        body = {'id': PARENT, 'fqdn': 'www.example.com',
                'status': 'ACTIVE', 'tls': 'letsencrypt',
                'dns_records': []}
        self.register('GET', PATH + '/domains', json={'domains': [body]})
        columns, rows = self.execute(domain.ListDomain, SCOPED)
        self.assertEqual(('ID', 'Status', 'FQDN', 'TLS', 'Primary'), columns)
        self.assertEqual(
            [(PARENT, 'ACTIVE', 'www.example.com', 'letsencrypt', '')],
            list(rows))
        self.assert_request('GET', PATH + '/domains')
        self.register('GET', PATH + '/domains/' + PARENT,
                      json={'domain': body})
        self.app.stdout = io.StringIO()
        self.execute(domain.ShowDomain, [PARENT, *SCOPED, '-f', 'json'],
                     True)
        output = json.loads(self.app.stdout.getvalue())
        self.assertEqual('letsencrypt', output[utils.label('tls')])
        self.assertEqual(PARENT, output[utils.label('id')])
        self.assertEqual([], output[utils.label('dns_records')])
        self.assert_request('GET', PATH + '/domains/' + PARENT)

    def test_domain_tls_write_uses_microversion_12_only(self):
        self.register('POST', PATH + '/domains', status_code=201,
                      json={'domain': {'id': PARENT, 'tls': 'none'}})
        self.execute(domain.AddDomain,
                     ['www.example.com', *SCOPED, '--tls', 'none'])
        self.assertEqual(
            {'domain': {'fqdn': 'www.example.com', 'tls': 'none'}},
            self.http.last_request.json())
        self.assert_request('POST', PATH + '/domains',
                            {'domain': {'fqdn': 'www.example.com',
                                        'tls': 'none'}}, '1.2')
        self.assertEqual('1.0', self.api.microversion)

        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.1')
        self.register('POST', PATH + '/domains', status_code=201,
                      json={'domain': {'tls': 'letsencrypt'}})
        self.execute(domain.AddDomain,
                     ['www.example.com', *SCOPED, '--auto-dns',
                      '--tls', 'letsencrypt'])
        self.assert_request(
            'POST', PATH + '/domains',
            {'domain': {'fqdn': 'www.example.com', 'auto_dns': True,
                        'tls': 'letsencrypt'}}, '1.2')

        self.register('PATCH', PATH + '/domains/' + PARENT,
                      json={'domain': {'id': PARENT, 'tls': 'none'}})
        self.execute(domain.SetDomain, [PARENT, *SCOPED, '--tls', 'none'])
        self.assert_request(
            'PATCH', PATH + '/domains/' + PARENT,
            {'domain': {'tls': 'none'}}, '1.2')
        self.assertEqual('1.1', self.app.client_manager.coast.microversion)

        self.register('GET', PATH + '/domains', json={'domains': []})
        self.execute(domain.ListDomain, SCOPED)
        self.assert_request('GET', PATH + '/domains', version='1.1')

        before = len(self.http.request_history)
        self.assertRaises(
            SystemExit, self.execute, domain.AddDomain,
            ['www.example.com', *SCOPED, '--tls', 'off'])
        self.assertRaises(
            exceptions.CommandError, self.execute, domain.SetDomain,
            [PARENT, *SCOPED])
        self.assertEqual(before, len(self.http.request_history))


for _cls, _argv, _method, _path, _envelope, _body in CASES:
    for _status in (400, 401, 403, 404, 406, 409, 500, 503):
        setattr(Phase5Test, 'test_%s_http_%s' % (_cls.__name__, _status),
                error_test(_cls, _argv, _method, _path, _status))

for _cls, _argv, _method, _path in (
        (domain.ShowDomain, [PARENT, *SCOPED], 'GET',
         PATH + '/domains/' + PARENT),
        (domain.SetDomain, [PARENT, *SCOPED, '--tls', 'none'], 'PATCH',
         PATH + '/domains/' + PARENT)):
    for _status in (400, 401, 403, 404, 406, 409, 500, 503):
        setattr(Phase5Test, 'test_%s_http_%s' % (_cls.__name__, _status),
                error_test(_cls, _argv, _method, _path, _status))
