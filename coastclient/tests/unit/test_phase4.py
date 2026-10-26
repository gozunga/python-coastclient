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

"""Phase-4 wire, output, validation and failure tests."""
import io
import json
from types import SimpleNamespace

import fixtures
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from openstackclient import shell
from osc_lib import exceptions
from oslotest import base
import requests
from requests_mock.contrib import fixture

from coastclient.osc.v1 import deployment
from coastclient.osc.v1 import logs
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils
from coastclient.tests.unit import labels
from coastclient.tests.unit.test_commands import error_test
from coastclient.v1 import client

ROOT = 'https://app.example.com/'
BASE = ROOT + 'v1/'
SERVICE = '11111111-1111-4111-8111-111111111111'
DEPLOYMENT = '22222222-2222-4222-8222-222222222222'
PARENT = '33333333-3333-4333-8333-333333333333'
STACK = '44444444-4444-4444-8444-444444444444'
SERVER = '55555555-5555-4555-8555-555555555555'
PATH = 'services/' + SERVICE
COLLECTION = PATH + '/deployments'
ITEM = COLLECTION + '/' + DEPLOYMENT
CREATE = ['web', '--appgroup', PARENT, '--environment', PARENT,
          '--server', SERVER,
          '--repo-url', 'https://example.com/web.git']
SCOPED = ['--service', SERVICE]
BODY = {'service': {'name': 'web', 'appgroup_id': PARENT,
                    'environment_id': PARENT, 'server_id': SERVER,
                    'kind': 'app',
                    'source': {'type': 'git',
                               'repo_url': 'https://example.com/web.git'}}}
CASES = (
    (service.CreateApp, CREATE, 'POST', 'services', 'service', BODY),
    (service.ListService, [], 'GET', 'services?kind=service', 'services',
     None),
    (service.ListApp, [], 'GET', 'services?kind=app', 'services', None),
    (service.ShowService, [SERVICE], 'GET', PATH, 'service', None),
    (service.DeleteService, [SERVICE], 'DELETE', PATH, 'service', None),
    (service.StartService, [SERVICE], 'POST', PATH + '/action', 'service',
     {'start': {}}),
    (service.StopService, [SERVICE], 'POST', PATH + '/action', 'service',
     {'stop': {}}),
    (service.RestartService, [SERVICE], 'POST', PATH + '/action', 'service',
     {'restart': {}}),
    (service.SetService, [SERVICE, '--ports', '80'], 'PATCH', PATH, 'service',
     {'service': {'ports': '80'}}),
    (service.SetApp, [SERVICE, '--ports', '80', '--description', 'd'], 'PATCH',
     PATH, 'service', {'service': {'ports': '80', 'description': 'd'}}),
    (deployment.Deploy, [SERVICE], 'POST', COLLECTION, 'deployment',
     {'deployment': {}}),
    (deployment.ListDeployment, SCOPED, 'GET', COLLECTION,
     'deployments', None),
    (deployment.ShowDeployment, [DEPLOYMENT, *SCOPED], 'GET', ITEM,
     'deployment', None),
    (deployment.LogDeployment, [DEPLOYMENT, *SCOPED], 'GET', ITEM + '/log',
     'log', None),
    (deployment.RollbackDeployment, [DEPLOYMENT, *SCOPED], 'POST',
     ITEM + '/action', 'deployment', {'rollback': {}}),
    (deployment.CancelDeployment, [DEPLOYMENT, *SCOPED], 'POST',
     ITEM + '/action', 'deployment', {'cancel': {}}),
    (logs.ShowLogs, ['--service', SERVICE], 'GET', PATH + '/logs',
     'logs', None),
)


class Phase4Test(base.BaseTestCase):
    def setUp(self):
        super().setUp()
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

    def test_every_command_wire_and_output_both_versions(self):
        for version in ('1.0', '1.1'):
            self.app.client_manager.coast = client.Client(
                self.session, region_name='region', microversion=version)
            for cls, argv, method, path, envelope, body in CASES:
                response = {'id': PARENT, 'status': 'PENDING'}
                if envelope in ('services', 'deployments'):
                    response = [response]
                elif envelope == 'logs':
                    response = {'lines': [{'ts': 'now', 'stream': 'stdout',
                                           'line': 'masked output'}]}
                elif envelope == 'log':
                    response = {'deployment_id': DEPLOYMENT, 'complete': False,
                                'lines': []}
                self.register(method, path, status_code=(
                    200 if method == 'GET' else 202),
                    json={envelope: response})
                self.app.stdout = io.StringIO()
                before = len(self.http.request_history)
                options = argv + (['-f', 'json'] if method != 'DELETE' else [])
                self.execute(cls, options, True)
                if method == 'DELETE':
                    self.assertEqual('', self.app.stdout.getvalue())
                    self.assert_request(method, path, body, version)
                    continue
                output = json.loads(self.app.stdout.getvalue())
                if envelope == 'service' and method == 'GET':
                    self.assertEqual(labels.labelled(dict(
                        response, url=response.get('default_url'),
                        worker_ip=None)), output)
                elif method != 'GET' or envelope == 'deployment':
                    self.assertEqual(labels.labelled(response, cls), output)
                elif envelope == 'log':
                    self.assertEqual({'Deployment ID': DEPLOYMENT,
                                      'Complete': False, 'Lines': []}, output)
                elif envelope == 'logs':
                    self.assertEqual([{'Timestamp': 'now', 'Stream': 'stdout',
                                       'Line': 'masked output'}], output)
                else:
                    self.assertEqual(PARENT, output[0]['ID'])
                    self.assertEqual('PENDING', output[0]['Status'])
                self.assert_request(method, path, body,
                                    '1.11' if cls is service.CreateApp
                                    else version)
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_deployment_show_includes_fault_not_internal_metadata(self):
        reason = 'App is running but nothing answered on port 8080'
        response = {'id': DEPLOYMENT, 'status': 'FAILED',
                    'source_ref': None, 'links': [],
                    'frozen_spec': {'secret': 'hidden'},
                    'secret_refs': 'hidden', 'spec': {'secret': 'hidden'},
                    'image_digest': 'hidden',
                    'engine_ref': 'hidden', 'log_ref': 'hidden',
                    'fault': reason}
        self.register('GET', ITEM, json={'deployment': response})
        self.execute(deployment.ShowDeployment,
                     [DEPLOYMENT, *SCOPED, '-f', 'json'], True)
        output = json.loads(self.app.stdout.getvalue())
        self.assertEqual(reason, output[utils.label('fault')])
        self.assertEqual(DEPLOYMENT, output[utils.label('id')])
        self.assertEqual('FAILED', output[utils.label('status')])
        for hidden in ('frozen_spec', 'secret_refs', 'spec',
                       'image_digest', 'engine_ref', 'log_ref'):
            self.assertNotIn(utils.label(hidden), output)

    def test_shell_loading_and_nonzero_errors_every_command(self):
        auth = ['--os-auth-type', 'v3token', '--os-auth-url',
                'https://identity.example.com/v3', '--os-token', 'input-token',
                '--os-project-id', 'project', '--os-region-name', 'region']
        for cls, argv, method, path, envelope, body in CASES:
            if cls is deployment.Deploy:
                words = ['deploy']
            elif cls is logs.ShowLogs:
                words = ['logs']
            else:
                resource = ('deployment' if cls.__module__ != service.__name__
                            else 'app' if cls.__name__.endswith('App')
                            else 'service')
                verb = cls.__name__.removesuffix(resource.title()).lower()
                words = [resource, verb]
            app = shell.OpenStackShell()
            app.stdout = io.StringIO()
            app.stderr = io.StringIO()
            logger = self.useFixture(fixtures.FakeLogger(name='run', level=10))
            self.register(method, path, status_code=503,
                          json={'faultstring': 'remote-secret'})
            result = app.run([*auth, 'coast', *words, *argv])
            self.assertNotEqual(0, result)
            self.assertIn('HTTP 503', logger.output)
            self.assertNotIn('remote-secret', logger.output)
            self.assertEqual('', app.stdout.getvalue())

    def test_deployment_log_nested_lines_and_complete(self):
        lines = [{'ts': 'now', 'stream': 'build', 'line': 'masked output'}]
        self.register('GET', ITEM + '/log', json={'log': {
            'deployment_id': DEPLOYMENT, 'complete': True, 'lines': lines}})
        self.execute(deployment.LogDeployment,
                     [DEPLOYMENT, *SCOPED, '-f', 'json'], True)
        self.assertEqual(labels.labelled({'Deployment ID': DEPLOYMENT,
                                          'Complete': True, 'Lines': lines}),
                         json.loads(self.app.stdout.getvalue()))

    def test_source_types_and_optional_fields(self):
        for kind, options, source in (
                ('git', ['--repo-url', 'https://example.com/a',
                         '--branch', 'dev',
                         '--build-pack', 'dockerfile',
                         '--dockerfile-path', 'D',
                         '--compose-path', 'C', '--base-directory', 'src'],
                 {'type': 'git', 'repo_url': 'https://example.com/a',
                  'branch': 'dev', 'build_pack': 'dockerfile',
                  'dockerfile_path': 'D', 'compose_path': 'C',
                  'base_directory': 'src'}),
                ('image', ['--image', 'registry.example.com/web',
                           '--tag', 'release'],
                 {'type': 'image', 'image': 'registry.example.com/web',
                  'tag': 'release'}),
                ('template', ['--template', 'postgresql'],
                 {'type': 'template', 'template_id': 'postgresql'})):
            self.register('POST', 'services', status_code=202,
                          json={'service': {'id': SERVICE}})
            cls = (service.CreateService if kind == 'template'
                   else service.CreateApp)
            self.execute(cls, [
                *CREATE[:7], *options,
                '--server', SERVICE, '--description', 'description',
                '--ports', '3000', '--cpu-limit', '0.5',
                '--memory-limit-mb', '512', '--health-check-path', '/health',
                '--no-deploy'])
            expected = {
                'name': 'web', 'appgroup_id': PARENT,
                'environment_id': PARENT, 'server_id': SERVICE,
                'source': source, 'description': 'description',
                'ports': '3000',
                'cpu_limit': 0.5, 'memory_limit_mb': 512,
                'healthcheck_path': '/health', 'deploy': False}
            if cls is service.CreateApp:
                expected['kind'] = 'app'
            self.assert_request('POST', 'services', {'service': expected},
                                '1.11' if cls is service.CreateApp else '1.4')

    def test_git_source_updates(self):
        self.register('GET', PATH, json={'service': {
            'source': {'type': 'git', 'branch': 'old'}}})
        self.register('PATCH', PATH, json={'service': {}})
        fields = {'branch': 'dev', 'dockerfile_path': 'Dockerfile',
                  'compose_path': 'compose.yaml', 'base_directory': 'src'}
        for pack in ('nixpacks', 'dockerfile', 'compose', 'static'):
            for key, value in dict(fields, build_pack=pack).items():
                self.execute(service.SetApp, [
                    SERVICE, '--' + key.replace('_', '-'), value])
                self.assert_request('PATCH', PATH, {
                    'service': {'source': {key: value}}})

    def test_git_options_require_git_source(self):
        for source_type in ('image', 'template'):
            self.register('GET', PATH, json={'service': {
                'source': {'type': source_type}}})
            error = self.assertRaises(
                exceptions.CommandError, self.execute, service.SetApp,
                [SERVICE, '--branch', 'dev'])
            self.assertEqual('Git source options require a Git app.',
                             str(error))
            self.assertEqual('GET', self.http.last_request.method)
        self.assertRaises(
            exceptions.CommandError, self.execute, service.SetApp,
            [SERVICE, '--branch', 'dev', '--image', 'nginx'])
        for options in (['--repo-url', 'https://example.com/web.git'],
                        ['--build-pack', 'invalid']):
            self.assertRaises(SystemExit, self.execute, service.SetApp,
                              [SERVICE, *options])

    def test_clear_resource_limits(self):
        self.register('PATCH', PATH, json={'service': {}})
        for cls in (service.SetApp, service.SetService):
            for flags, values in (
                    (['--no-cpu-limit'], {'cpu_limit': None}),
                    (['--no-memory-limit'], {'memory_limit_mb': None}),
                    (['--no-cpu-limit', '--no-memory-limit'],
                     {'cpu_limit': None, 'memory_limit_mb': None}),
                    (['--cpu-limit', '0.25'], {'cpu_limit': 0.25})):
                self.execute(cls, [SERVICE, *flags])
                self.assert_request('PATCH', PATH, {'service': values})
            for setter, clearer in (
                    ('--cpu-limit', '--no-cpu-limit'),
                    ('--memory-limit-mb', '--no-memory-limit')):
                self.assertRaises(SystemExit, self.execute, cls,
                                  [SERVICE, setter, '1', clearer])
            for value in ('0', '-1', 'nan', 'inf', 'invalid'):
                self.assertRaises(SystemExit, self.execute, cls,
                                  [SERVICE, '--cpu-limit', value])

    def test_create_worker_names_are_scoped_to_appgroup_stack(self):
        self.register('GET', 'stacks', json={'stacks': [
            {'id': STACK}, {'id': DEPLOYMENT}]})
        self.register('GET', 'appgroups/' + PARENT, json={
            'appgroup': {'id': PARENT, 'stack_id': STACK}})
        self.register('GET', 'stacks/' + STACK + '/servers', json={
            'servers': [{'id': SERVER, 'name': 'worker-1'}]})
        self.register('GET', 'stacks/' + DEPLOYMENT + '/servers', json={
            'servers': [{'id': SERVICE, 'name': 'worker-1'}]})
        self.register('POST', 'services', json={'service': {}})
        for cls, source in (
                (service.CreateApp,
                 ['--repo-url', 'https://example.com/web.git']),
                (service.CreateService, ['--template', 'postgresql'])):
            self.execute(cls, [
                'web', '--appgroup', PARENT, '--environment', PARENT,
                '--server', 'worker-1', *source])
            self.assertEqual(
                SERVER, self.http.last_request.json()['service']['server_id'])
        self.assertFalse(any(
            '/stacks/' + DEPLOYMENT + '/servers' in request.url
            for request in self.http.request_history))

    def test_app_create_sources_are_apps(self):
        for options in (
                ['--image', 'nginx', '--tag', 'alpine'],
                ['--repo-url', 'https://example.com/web.git'],
                ['--repo-url', 'https://example.com/web.git',
                 '--build-pack', 'compose', '--compose-path', 'compose.yaml']):
            with self.subTest(options=options):
                self.register('POST', 'services', status_code=202,
                              json={'service': {'id': SERVICE, 'kind': 'app'}})
                self.execute(service.CreateApp, [*CREATE[:7], *options])
                self.assertEqual(
                    'app', self.http.last_request.json()['service']['kind'])
                self.assertEqual(
                    'app-hosting 1.11',
                    self.http.last_request.headers['OpenStack-API-Version'])
                self.assertEqual('1.0', self.api.microversion)

    def test_app_list_filter_and_show_kind(self):
        row = {'id': SERVICE, 'name': 'web', 'kind': 'app'}
        self.register('GET', 'services?kind=app', complete_qs=True,
                      json={'services': [row]})
        columns, rows = self.execute(service.ListApp, [])
        self.assertEqual(SERVICE, list(rows)[0][columns.index('ID')])
        self.assert_request('GET', 'services?kind=app')
        self.register('GET', PATH, json={'service': row})
        self.register('GET', PATH + '/webhooks', status_code=403)
        columns, values = self.execute(service.ShowApp, [SERVICE])
        self.assertEqual('app',
                         dict(zip(columns, values))[utils.label('kind')])

    def test_service_create_from_image_is_a_service(self):
        # Regression: an image service must be created as kind service.
        self.register('POST', 'services', status_code=202,
                      json={'service': {'id': SERVICE}})
        self.execute(service.CreateService, [
            *CREATE[:7], '--image', 'nginx', '--tag', 'alpine',
            '--server', SERVICE])
        self.assert_request('POST', 'services', {'service': {
            'name': 'web', 'appgroup_id': PARENT, 'environment_id': PARENT,
            'server_id': SERVICE, 'kind': 'service',
            'source': {'type': 'image', 'image': 'nginx', 'tag': 'alpine'}}},
            '1.11')
        before = len(self.http.request_history)
        for options in (['--template', 'nginx', '--image', 'nginx',
                         '--tag', 'x'], []):
            self.assertRaises(exceptions.CommandError, self.execute,
                              service.CreateService,
                              [*CREATE[:7], *options])
        self.assertEqual(before, len(self.http.request_history))

    def test_template_id_must_be_a_slug(self):
        for bad in ('', 'a b', 'x/y', 'x' * 256):
            before = len(self.http.request_history)
            self.assertRaises(SystemExit, self.execute, service.CreateService,
                              [*CREATE[:7], '--template', bad])
            self.assertEqual(before, len(self.http.request_history))

    def test_source_incompatibility_and_missing_options_no_http(self):
        for options in (
                CREATE[:7],
                CREATE + ['--image', 'image'],
                CREATE[:7] + ['--image', 'image'],
                CREATE[:7] + ['--tag', 'release']):
            before = len(self.http.request_history)
            self.assertRaises(exceptions.CommandError, self.execute,
                              service.CreateApp, options)
            self.assertEqual(before, len(self.http.request_history))

    def test_invalid_arguments_no_http(self):
        for cls, argv, method, path, envelope, body in CASES:
            invalid = [[*argv, '--wait']]
            if cls not in (service.ListService, service.ListApp):
                invalid.append([])
            if cls is service.CreateApp:
                invalid += [CREATE + ['--cpu-limit', n]
                            for n in ('0', '-1', 'nan', 'inf', 'bad')]
                invalid += [CREATE + ['--memory-limit-mb', '0'],
                            CREATE + ['--env', 'SECRET=value']]
            elif cls in (service.ListService, service.ListApp):
                invalid += [['--limit', '0']]
            elif cls.__module__ == deployment.__name__ and (
                    cls is not deployment.Deploy):
                invalid += [['name', *argv[1:]]]
            if cls is logs.ShowLogs:
                invalid += [['--service', SERVICE, '--tail', '-1']]
            for options in invalid:
                before = len(self.http.request_history)
                self.assertRaises(SystemExit, self.execute, cls, options)
                self.assertEqual(before, len(self.http.request_history))

    def test_scoped_logs_and_webhooks_reject_positional_aliases(self):
        for cls, flag in ((logs.ShowLogs, '--service'),
                          (service.ShowAppWebhook, '--app'),
                          (service.RegenerateAppWebhook, '--app')):
            parser = cls(self.app, None).get_parser('test')
            for target in ('web', SERVICE):
                self.assertEqual(target, parser.parse_args(
                    [flag, target]).service)
                for argv in ([], [target], [flag, target, target]):
                    before = len(self.http.request_history)
                    self.assertRaises(SystemExit, self.execute, cls, argv)
                    self.assertEqual(before, len(self.http.request_history))

    def test_logs_filters_and_deploy_ref(self):
        self.register('GET', PATH + '/logs', json={'logs': {'lines': []}})
        self.execute(logs.ShowLogs,
                     ['--service', SERVICE, '--tail', '0',
                      '--since', 'invalid'])
        self.assertEqual({'tail': ['0'], 'since': ['invalid']},
                         self.http.last_request.qs)
        self.register('POST', COLLECTION, status_code=202,
                      json={'deployment': {'id': PARENT}})
        self.execute(deployment.Deploy, [SERVICE, '--source-ref', 'release'])
        self.assert_request('POST', COLLECTION,
                            {'deployment': {'source_ref': 'release'}})

    def test_empty_write_responses_and_transport_failures(self):
        for cls, argv, method, path, envelope, body in CASES:
            if method != 'GET':
                self.app.stdout = io.StringIO()
                self.register(method, path, status_code=202, text='')
                if method == 'DELETE':
                    self.execute(cls, argv)
                else:
                    error = self.assertRaises(exceptions.CommandError,
                                              self.execute, cls, argv)
                    self.assertEqual('Invalid Coast API response.', str(error))
                self.assertEqual('', self.app.stdout.getvalue())
            for outcome in ({'exc': requests.exceptions.ConnectionError},
                            {'status_code': 200, 'text': 'remote-secret'}):
                self.register(method, path, **outcome)
                before = len(self.http.request_history)
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls, argv)
                self.assertNotIn('remote-secret', str(error))
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_list_pagination_columns_filters_and_empty(self):
        self.register('GET', 'appgroups/' + PARENT,
                      json={'appgroup': {'id': PARENT}})
        self.register('GET', 'environments/' + PARENT,
                      json={'environment': {'id': PARENT,
                                            'appgroup_id': PARENT}})
        for cls, argv, path, envelope, columns in (
                (service.ListService, ['--appgroup', PARENT,
                                       '--environment', PARENT],
                 'services', 'services',
                 ('ID', 'Name', 'Status', 'App group ID', 'Environment ID')),
                (deployment.ListDeployment, SCOPED, COLLECTION, 'deployments',
                 ('ID', 'Status', 'Number', 'Source ref', 'Resolved ref',
                  'Trigger'))):
            self.register('GET', path, json={envelope: [{'id': SERVICE}],
                          'links': [{'rel': 'next',
                                     'href': '?marker=' + DEPLOYMENT}]})
            self.register('GET', path + '?marker=' + DEPLOYMENT,
                          complete_qs=True, json={envelope: []})
            cols, rows = self.execute(cls, [*argv, '--limit', '1'])
            self.assertEqual(columns, cols)
            self.assertEqual(SERVICE, list(rows)[0][0])
            first = self.http.request_history[-2]
            self.assertEqual(['1'], first.qs['limit'])
            if envelope == 'services':
                self.assertEqual([PARENT], first.qs['appgroup_id'])
                self.assertEqual([PARENT], first.qs['environment_id'])
            self.register('GET', path, json={envelope: []})
            cols, rows = self.execute(cls, argv)
            self.assertEqual([], list(rows))

    def test_show_adds_url_and_worker_floating_ip(self):
        service_body = {
            'id': SERVICE, 'name': 'web', 'status': 'ACTIVE',
            'stack_id': STACK, 'server_id': SERVER,
            'default_url': 'https://web-1111.apps.example.com',
            'dns_records': [{'type': 'A', 'name': 'web',
                             'value': '203.0.113.9', 'purpose': 'routing'}]}
        server_path = 'stacks/%s/servers/%s' % (STACK, SERVER)
        self.register('GET', PATH, json={'service': service_body})
        self.register('GET', server_path, json={'server': {
            'id': SERVER, 'floating_ip': '203.0.113.10',
            'status': 'ACTIVE'}})
        self.register('GET', PATH + '/webhooks', json={'webhooks': {
            'enabled': True, 'token': 't' * 43,
            'deploy': 'http://api/v1/apps/x/deploy?token=t',
            'github': 'http://api/v1/hooks/x/github?token=t'}})
        for cls in (service.ShowService, service.ShowApp):
            before = len(self.http.request_history)
            self.app.stdout = io.StringIO()
            self.execute(cls, [SERVICE, '-f', 'json'], True)
            output = json.loads(self.app.stdout.getvalue())
            self.assertEqual('https://web-1111.apps.example.com',
                             output[utils.label('url')])
            self.assertEqual(output[utils.label('default_url')],
                             output[utils.label('url')])
            self.assertEqual('203.0.113.10', output[utils.label('worker_ip')])
            self.assertEqual('web', output[utils.label('name')])
            self.assertEqual('ACTIVE', output[utils.label('status')])
            self.assertEqual(cls is service.ShowApp,
                             utils.label('webhook_deploy') in output)
            self.assertNotIn(utils.label('webhook_token'), output)
            self.assertEqual(service_body['dns_records'],
                             output[utils.label('dns_records')])
            self.assertEqual(2 + (cls is service.ShowApp),
                             len(self.http.request_history) - before)
            self.assert_request('GET', server_path)

    def test_worker_ip_falls_back_to_server_list_then_dns(self):
        records = [
            {'type': 'CNAME', 'value': 'web.example.com'},
            {'type': 'AAAA', 'value': '2001:db8::1'},
            {'type': 'A', 'value': '203.0.113.9'}]
        service_body = {
            'id': SERVICE, 'name': 'web', 'status': 'ACTIVE',
            'stack_id': STACK, 'server_id': SERVER,
            'default_url': 'https://web-1111.apps.example.com',
            'dns_records': records}
        server_path = 'stacks/%s/servers/%s' % (STACK, SERVER)
        list_path = 'stacks/%s/servers' % STACK
        self.register('GET', PATH, json={'service': service_body})
        self.register('GET', server_path, status_code=404,
                      json={'faultstring': 'remote-secret'})
        self.register('GET', list_path, json={'servers': [
            {'id': PARENT, 'floating_ip': '198.51.100.1'},
            {'id': SERVER, 'floating_ip': '198.51.100.8'}]})
        self.app.stdout = io.StringIO()
        self.execute(service.ShowService, [SERVICE, '-f', 'json'], True)
        output = json.loads(self.app.stdout.getvalue())
        self.assertEqual('198.51.100.8', output[utils.label('worker_ip')])
        self.assertNotIn('remote-secret', self.app.stdout.getvalue())
        self.assert_request('GET', list_path)

        self.register('GET', server_path, json={'server': {
            'id': SERVER, 'floating_ip': None}})
        before = len(self.http.request_history)
        self.app.stdout = io.StringIO()
        self.execute(service.ShowService, [SERVICE, '-f', 'json'], True)
        output = json.loads(self.app.stdout.getvalue())
        self.assertEqual('203.0.113.9', output[utils.label('worker_ip')])
        self.assertEqual(2, len(self.http.request_history) - before)
        self.assert_request('GET', server_path)

        self.register('GET', server_path, status_code=503,
                      json={'faultstring': 'remote-secret'})
        self.register('GET', list_path, status_code=503,
                      json={'faultstring': 'remote-secret'})
        self.app.stdout = io.StringIO()
        self.execute(service.ShowService, [SERVICE, '-f', 'json'], True)
        output = json.loads(self.app.stdout.getvalue())
        self.assertEqual('203.0.113.9', output[utils.label('worker_ip')])
        self.assertNotIn('remote-secret', self.app.stdout.getvalue())

        no_server = dict(service_body, server_id=None, stack_id=None,
                         dns_records=[{'type': 'TXT', 'value': 'token'}])
        self.register('GET', PATH, json={'service': no_server})
        before = len(self.http.request_history)
        self.app.stdout = io.StringIO()
        self.register('GET', PATH + '/webhooks', status_code=403)
        self.execute(service.ShowApp, [SERVICE, '-f', 'json'], True)
        output = json.loads(self.app.stdout.getvalue())
        self.assertIsNone(output[utils.label('worker_ip')])
        self.assertEqual(no_server['default_url'], output[utils.label('url')])
        # ShowApp also asks for webhook URLs.
        self.assertEqual(2, len(self.http.request_history) - before)

    def test_deployment_list_fault_column_is_optional(self):
        reason = 'nothing answered on port 8080'
        row = {'id': DEPLOYMENT, 'number': 2, 'status': 'FAILED',
               'source_ref': 'main', 'resolved_ref': 'abc',
               'trigger': 'api', 'fault': reason}
        self.register('GET', COLLECTION, json={'deployments': [row]})
        cols, rows = self.execute(deployment.ListDeployment, SCOPED)
        self.assertEqual(('ID', 'Status', 'Number', 'Source ref',
                          'Resolved ref', 'Trigger'),
                         cols)
        self.assertNotIn('Fault', cols)
        self.assertNotIn(reason, list(rows)[0])

        before = len(self.http.request_history)
        self.app.stdout = io.StringIO()
        self.execute(deployment.ListDeployment,
                     [*SCOPED, '-c', 'fault', '-f', 'json'], True)
        self.assertEqual(labels.labelled([{'Fault': reason}]),
                         json.loads(self.app.stdout.getvalue()))
        self.assertEqual(1, len(self.http.request_history) - before)

        self.app.stdout = io.StringIO()
        self.execute(deployment.ListDeployment,
                     [*SCOPED, '-c', 'ID', '-c', 'Fault', '-f', 'json'],
                     True)
        self.assertEqual([{'ID': DEPLOYMENT, 'Fault': reason}],
                         json.loads(self.app.stdout.getvalue()))

    def test_create_without_port(self):
        for cls, argv in (
                (service.CreateApp, CREATE),
                (service.CreateService,
                 [*CREATE[:7], '--template', 'postgresql'])):
            with self.subTest(command=cls.__name__):
                self.register('POST', 'services',
                              json={'service': {'id': SERVICE}})
                self.execute(cls, argv)
                self.assertNotIn(
                    'ports', self.http.last_request.json()['service'])

    def test_legacy_port_options_are_rejected(self):
        for cls, argv in ((service.CreateApp, CREATE),
                          (service.CreateService, CREATE[:7] +
                           ['--template', 'postgresql']),
                          (service.SetApp, [SERVICE]),
                          (service.SetService, [SERVICE])):
            for option in (['--port', '8080'], ['--no-port']):
                with self.subTest(command=cls.__name__, option=option):
                    before = len(self.http.request_history)
                    self.assertRaises(SystemExit, self.execute, cls,
                                      [*argv, *option])
                    self.assertEqual(before, len(self.http.request_history))

    def test_set_optional_port(self):
        for cls in (service.SetService, service.SetApp):
            for options, expected in (
                    (['--description', 'updated'],
                     {'description': 'updated'}),
                    (['--ports', '8080,9090'], {'ports': '8080,9090'}),
                    (['--ports', ''], {'ports': None}),
                    (['--ports', '   '], {'ports': None}),
                    (['--no-ports'], {'ports': None})):
                with self.subTest(command=cls.__name__, options=options):
                    self.register('PATCH', PATH,
                                  json={'service': {'id': SERVICE}})
                    self.execute(cls, [SERVICE, *options])
                    self.assert_request('PATCH', PATH,
                                        {'service': expected})

    def test_set_rejects_conflicting_port_options(self):
        for cls in (service.SetService, service.SetApp):
            before = len(self.http.request_history)
            self.assertRaises(SystemExit, self.execute, cls,
                              [SERVICE, '--ports', '8080', '--no-ports'])
            self.assertEqual(before, len(self.http.request_history))

    def test_health_check_create_and_set(self):
        for cls, argv, method, path in (
                (service.CreateApp, CREATE, 'POST', 'services'),
                (service.CreateService,
                 [*CREATE[:7], '--template', 'postgresql'],
                 'POST', 'services'),
                (service.SetApp, [SERVICE], 'PATCH', PATH),
                (service.SetService, [SERVICE], 'PATCH', PATH)):
            for mode in ('none', 'http'):
                self.register(method, path, json={'service': {'id': SERVICE}})
                extra = ['--health-check', mode]
                if mode == 'http':
                    extra += ['--health-check-port', '8080',
                              '--health-check-path', '/ready']
                self.execute(cls, [*argv, *extra])
                values = self.http.last_request.json()['service']
                expected = {}
                if method == 'POST':
                    expected = dict(BODY['service'])
                    if cls is service.CreateService:
                        expected.pop('kind')
                        expected['source'] = {
                            'type': 'template', 'template_id': 'postgresql'}
                expected['health_check'] = mode
                if mode == 'http':
                    expected.update(health_check_port=8080,
                                    healthcheck_path='/ready')
                self.assertEqual({'service': expected},
                                 self.http.last_request.json())
                self.assertEqual(mode, values['health_check'])
                self.assertNotIn('ports', values)
                if mode == 'http':
                    self.assertEqual(8080, values['health_check_port'])
                    self.assertEqual('/ready', values['healthcheck_path'])
                else:
                    self.assertNotIn('health_check_port', values)
                    self.assertNotIn('healthcheck_path', values)
                self.assert_request(method, path, {'service': values},
                                    '1.11' if cls is service.CreateApp
                                    else '1.4')
                self.assertEqual('1.0', self.api.microversion)

    def test_set_clears_health_check_port(self):
        for cls in (service.SetApp, service.SetService):
            for extra in ([], ['--health-check', 'http'],
                          ['--health-check', 'none']):
                self.register('PATCH', PATH,
                              json={'service': {'id': SERVICE}})
                self.execute(cls, [SERVICE, '--no-health-check-port', *extra])
                expected = {'health_check_port': None}
                if extra:
                    expected['health_check'] = extra[1]
                self.assert_request(
                    'PATCH', PATH, {'service': expected}, '1.4')
            before = len(self.http.request_history)
            self.assertRaises(
                SystemExit, self.execute, cls,
                [SERVICE, '--health-check-port', '8080',
                 '--no-health-check-port'])
            self.assertEqual(before, len(self.http.request_history))
            help_text = ' '.join(
                cls(self.app, None).get_parser('test').format_help().split())
            self.assertIn('Clear the stored HTTP probe port', help_text)
            self.assertIn('first app port', help_text)

    def test_probe_options_reject_none_without_request(self):
        for cls, argv in ((service.CreateApp, CREATE),
                          (service.SetApp, [SERVICE]),
                          (service.CreateService,
                           [*CREATE[:7], '--template', 'postgresql']),
                          (service.SetService, [SERVICE])):
            for option, value in (('--health-check-port', '80'),
                                  ('--health-check-path', '/ready')):
                for extra in (['--health-check', 'none'],):
                    before = len(self.http.request_history)
                    self.assertRaises(
                        exceptions.CommandError, self.execute, cls,
                        [*argv, *extra, option, value])
                    self.assertEqual(before, len(self.http.request_history))

    def test_standalone_probe_and_memory_options(self):
        for cls, argv, method, path, version in (
                (service.CreateApp, CREATE, 'POST', 'services', '1.11'),
                (service.CreateService,
                 [*CREATE[:7], '--template', 'postgresql'],
                 'POST', 'services', '1.4'),
                (service.CreateService,
                 [*CREATE[:7], '--image', 'nginx', '--tag', 'stable'],
                 'POST', 'services', '1.11'),
                (service.SetApp, [SERVICE], 'PATCH', PATH, '1.4'),
                (service.SetService, [SERVICE], 'PATCH', PATH, '1.4')):
            for option, value, field, expected in (
                    ('--health-check-path', '/ready',
                     'healthcheck_path', '/ready'),
                    ('--health-check-port', '8080', 'health_check_port', 8080),
                    ('--memory-limit-mb', '512', 'memory_limit_mb', 512)):
                with self.subTest(command=cls.__name__, option=option):
                    self.register(method, path,
                                  json={'service': {'id': SERVICE}})
                    self.execute(cls, [*argv, option, value])
                    values = self.http.last_request.json()['service']
                    self.assertEqual(expected, values[field])
                    self.assertNotIn('health_check', values)
                    self.assertNotIn('health_check_path', values)
                    if field != 'memory_limit_mb':
                        self.assert_request(method, path, {'service': values},
                                            version)
            for options in (['--healthcheck-path', '/ready'],
                            ['--memory-limit', '512'],
                            ['--memory-limit-mb', '0'],
                            ['--memory-limit-mb', '-1'],
                            ['--memory-limit-mb', '1.5']):
                before = len(self.http.request_history)
                self.assertRaises(SystemExit, self.execute, cls,
                                  [*argv, *options])
                self.assertEqual(before, len(self.http.request_history))

    def test_workload_help_uses_final_names(self):
        for cls in (service.CreateApp, service.CreateService,
                    service.SetApp, service.SetService):
            help_text = cls(self.app, None).get_parser('test').format_help()
            for old in ('--health-check-type', '--healthcheck-path',
                        '--default-address', '--memory-limit '):
                self.assertNotIn(old, help_text)
            self.assertIn('--health-check-path', help_text)
            self.assertIn('--memory-limit-mb', help_text)

    def test_default_domain_flags_reject_old_and_conflicting_options(self):
        for cls in (service.SetApp, service.SetService):
            for options in (['--default-address', 'on'],
                            ['--enable-default-domain',
                             '--disable-default-domain']):
                before = len(self.http.request_history)
                self.assertRaises(SystemExit, self.execute, cls,
                                  [SERVICE, *options])
                self.assertEqual(before, len(self.http.request_history))

    def test_health_check_parser_invalid_values(self):
        for options in (['--health-check', 'tcp'],
                        ['--health-check-port', '0'],
                        ['--health-check-port', '65536'],
                        ['--health-check-port', 'abc']):
            self.assertRaises(SystemExit, self.execute, service.CreateApp,
                              [*CREATE, *options])

    def test_create_ports_and_http_defaults(self):
        self.register('POST', 'services', json={'service': {'id': SERVICE}})
        self.execute(service.CreateApp,
                     [*CREATE, '--health-check', 'http', '--ports', '8080'])
        values = self.http.last_request.json()['service']
        self.assertEqual('8080', values['ports'])
        self.assertEqual('http', values['health_check'])
        self.assertNotIn('health_check_port', values)
        self.assertNotIn('healthcheck_path', values)

    def test_health_check_show_and_list_when_present(self):
        self.register('GET', PATH + '/webhooks', status_code=403)
        for show, listing in ((service.ShowApp, service.ListApp),
                              (service.ShowService, service.ListService)):
            self.register('GET', PATH, json={'service': {
                'id': SERVICE, 'health_check': 'none'}})
            columns, values = self.execute(show, [SERVICE])
            self.assertEqual(
                'none', dict(zip(columns, values))['Health check'])
            self.register('GET', 'services', json={'services': [
                {'id': SERVICE, 'health_check': 'http'}, {'id': PARENT}]})
            columns, rows = self.execute(listing, [])
            rows = list(rows)
            self.assertIn('Health check', columns)
            self.assertEqual('http', rows[0][columns.index('Health check')])


for _cls, _argv, _method, _path, _envelope, _body in CASES:
    for _status in (400, 401, 403, 404, 406, 409, 500, 503):
        setattr(Phase4Test, 'test_%s_http_%s' % (_cls.__name__, _status),
                error_test(_cls, _argv, _method, _path, _status))

    def test_webhook_show_and_regenerate(self):
        hooks = {'enabled': True, 'token': 't' * 43,
                 'deploy': 'http://api/v1/apps/x/deploy?token=t',
                 'gitlab': 'http://api/v1/hooks/x/gitlab?token=t'}
        self.register('GET', PATH, json={'service': {'id': SERVICE}})
        self.register('GET', PATH + '/webhooks', json={'webhooks': hooks})
        self.register('POST', PATH + '/webhooks/regenerate',
                      json={'webhooks': dict(hooks, deploy='new')})
        cols, rows = self.execute(service.ShowAppWebhook, ['--app', SERVICE])
        self.assertEqual(('deploy', 'gitlab'), tuple(cols))
        self.assertEqual('1.3', self.http.last_request.headers[
            'OpenStack-API-Version'].split()[-1])
        cols, rows = self.execute(service.RegenerateAppWebhook,
                                  ['--app', SERVICE])
        self.assertIn('new', rows)
        self.assertEqual('POST', self.http.last_request.method)
