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

"""Exercise commands with real adapters and requests-mock HTTP fixtures."""
import io
import json
from types import SimpleNamespace

import fixtures
from keystoneauth1 import exceptions as ks_exc
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from openstackclient import shell
from osc_lib import exceptions
from oslotest import base
import requests
from requests_mock.contrib import fixture
import yaml

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.osc.v1 import utils
from coastclient.tests.unit import labels
from coastclient.v1 import client

ROOT = 'https://app.example.com/'
BASE = ROOT + 'v1/'
STACK = '11111111-1111-4111-8111-111111111111'
SERVER = '22222222-2222-4222-8222-222222222222'
STACK_PATH = 'stacks/' + STACK
SERVERS_PATH = STACK_PATH + '/servers'
SERVER_PATH = SERVERS_PATH + '/' + SERVER
APPGROUP = '33333333-3333-4333-8333-333333333333'
ENVIRONMENT = '44444444-4444-4444-8444-444444444444'
APPGROUPS_PATH = STACK_PATH + '/appgroups'
APPGROUP_PATH = 'appgroups/' + APPGROUP
ENVIRONMENTS_PATH = APPGROUP_PATH + '/environments'
ENVIRONMENT_PATH = 'environments/' + ENVIRONMENT
SCOPED = ['--stack', STACK]
ADD = ['worker', *SCOPED, '--kind', 'vm', '--role', 'worker',
       '--plan', 'worker']
CASES = (
    (stack.CreateStack, ['demo', '--plan', 'control'],
     'POST', 'stacks', 'stack'),
    (stack.ListStack, [], 'GET', 'stacks', 'stacks'),
    (stack.ShowStack, [STACK], 'GET', STACK_PATH, 'stack'),
    (stack.DeleteStack, [STACK], 'DELETE', STACK_PATH, 'stack'),
    (server.AddServer, ADD, 'POST', SERVERS_PATH, 'server'),
    (server.ListServer, SCOPED, 'GET', SERVERS_PATH, 'servers'),
    (server.RemoveServer, [SERVER, *SCOPED], 'DELETE', SERVER_PATH, 'server'),
)

CHILD_CASES = (
    (appgroup.CreateAppgroup, ['demo', *SCOPED],
     'POST', APPGROUPS_PATH, 'appgroup'),
    (appgroup.ListAppgroup, SCOPED,
     'GET', APPGROUPS_PATH, 'appgroups'),
    (appgroup.ShowAppgroup, [APPGROUP],
     'GET', APPGROUP_PATH, 'appgroup'),
    (appgroup.DeleteAppgroup, [APPGROUP],
     'DELETE', APPGROUP_PATH, 'appgroup'),
    (environment.CreateEnvironment, ['demo', '--appgroup', APPGROUP],
     'POST', ENVIRONMENTS_PATH, 'environment'),
    (environment.ListEnvironment, ['--appgroup', APPGROUP],
     'GET', ENVIRONMENTS_PATH, 'environments'),
    (environment.ShowEnvironment, [ENVIRONMENT],
     'GET', ENVIRONMENT_PATH, 'environment'),
    (environment.DeleteEnvironment, [ENVIRONMENT],
     'DELETE', ENVIRONMENT_PATH, 'environment'),
)
CASES += CHILD_CASES


class CommandTest(base.BaseTestCase):

    def setUp(self):
        super().setUp()
        self.set_up_api()

    def set_up_api(self):
        self.http = self.useFixture(fixture.Fixture())
        self.register('GET', 'services', json={'services': []})
        self.register('GET', ENVIRONMENTS_PATH, json={'environments': []})
        self.addCleanup(self.assert_no_top_level_parent_lists)
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

    def assert_no_top_level_parent_lists(self):
        for request in self.http.request_history:
            if request.method == 'GET':
                self.assertNotIn(request.url.split('?')[0],
                                 (BASE + 'appgroups', BASE + 'environments'))

    def execute(self, cls, argv, formatted=False):
        cmd = cls(self.app, None)
        args = cmd.get_parser('test').parse_args(argv)
        return cmd.run(args) if formatted else cmd.take_action(args)

    def register(self, method, path, **kwargs):
        return self.http.register_uri(method, BASE + path, **kwargs)

    def assert_request(self, method, path, body=None, version='1.0'):
        req = self.http.last_request
        if path in (APPGROUP_PATH, ENVIRONMENT_PATH) and method == 'GET':
            req = next(item for item in reversed(self.http.request_history)
                       if item.url == BASE + path and item.method == method)
        self.assertEqual(method, req.method)
        self.assertEqual(BASE + path, req.url)
        self.assertEqual('app-hosting ' + version,
                         req.headers['OpenStack-API-Version'])
        self.assertEqual('test-token', req.headers['X-Auth-Token'])
        if body is None:
            self.assertFalse(req.body)
        else:
            self.assertEqual(body, req.json())

    def test_stack_create_10_optional_fields_and_workers(self):
        response = {'id': STACK, 'status': 'PENDING', 'name': 'demo'}
        self.register('POST', 'stacks', status_code=202,
                      json={'stack': response})
        cols, data = self.execute(stack.CreateStack, [
            'demo', '--plan', 'control', '--description', 'description',
            '--worker', 'kind=vm,plan=one',
            '--worker', 'kind=baremetal,plan=two',
            '--build-server', '--data-volume-gb', '100'])
        self.assertEqual(labels.labelled(response), dict(zip(cols, data)))
        self.assert_request('POST', 'stacks', {'stack': {
            'name': 'demo', 'plan': 'control', 'description': 'description',
            'workers': [{'kind': 'vm', 'plan': 'one'},
                        {'kind': 'baremetal', 'plan': 'two'}],
            'build_server': True, 'data_volume_gb': 100}})

    def test_stack_create_help_explains_provisioning_defaults(self):
        parser = stack.CreateStack(self.app, None).get_parser('stack create')
        help_text = ' '.join(parser.format_help().split())
        for expected in (
                'Data volume size in GiB (positive integer); omit for the '
                'operator default.',
                'Provision a dedicated build server; omitted by default.',
                'App engine server image name or ID (API 1.1+); omit for '
                'the operator default.',
                'Build VM plan (API 1.1+); requires --build-server. Omit to '
                'select the unique build-compatible VM plan.',
                'Build VM image name or ID (API 1.1+); requires '
                '--build-server. Omit for the operator default.'):
            with self.subTest(help=expected):
                self.assertIn(expected, help_text)

    def test_stack_create_help_explains_dns_defaults_and_version(self):
        parser = stack.CreateStack(self.app, None).get_parser('stack create')
        help_text = ' '.join(parser.format_help().split())
        for expected in (
                'Default domain suffix; omit to use the selected zone name, '
                'if any (API 1.22+).',
                'DNS zone UUID; omit to select the most specific project '
                'zone matching the domain, if any (API 1.22+).',
                'Delegated DNS trust UUID; omit to use the caller project '
                '(API 1.22+).'):
            with self.subTest(help=expected):
                self.assertIn(expected, help_text)

    def test_stack_create_dns_selections_and_omission(self):
        self.http.get(ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.22', 'links': [
                {'rel': 'self', 'href': BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.22')
        self.app.client_manager.coast = self.api
        self.register('POST', 'stacks', status_code=202,
                      json={'stack': {'id': STACK}})
        self.execute(stack.CreateStack, [
            'demo', '--plan', 'control', '--default-domain', 'example.com',
            '--dns-zone', SERVER, '--dns-trust', APPGROUP])
        self.assert_request('POST', 'stacks', {'stack': {
            'name': 'demo', 'plan': 'control', 'default_domain': 'example.com',
            'dns_zone_id': SERVER, 'dns_trust_id': APPGROUP}}, version='1.22')
        self.execute(stack.CreateStack, ['demo', '--plan', 'control'])
        self.assert_request('POST', 'stacks', {'stack': {
            'name': 'demo', 'plan': 'control'}}, version='1.22')

    def test_stack_create_dns_validation_before_request(self):
        before = len(self.http.request_history)
        for option, value in (('--dns-zone', 'not-a-uuid'),
                              ('--dns-trust', 'not-a-uuid'),
                              ('--dns-z', SERVER)):
            self.assertRaises(SystemExit, self.execute, stack.CreateStack,
                              ['demo', '--plan', 'control', option, value])
        self.assertEqual(before, len(self.http.request_history))

    def test_stack_create_11_role_and_image_selections(self):
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.1')
        self.app.client_manager.coast = self.api
        self.register('POST', 'stacks', status_code=202,
                      json={'stack': {'id': STACK, 'status': 'PENDING'}})
        self.execute(stack.CreateStack, [
            'demo', '--plan', 'control', '--image', 'controller-image',
            '--worker', 'kind=vm,plan=worker,image=worker-image',
            '--build-server', '--build-server-plan', 'build',
            '--build-server-image', 'build-image'])
        self.assert_request('POST', 'stacks', {'stack': {
            'name': 'demo', 'plan': 'control', 'image': 'controller-image',
            'workers': [{'kind': 'vm', 'plan': 'worker',
                         'image': 'worker-image'}],
            'build_server': True, 'build_server_plan': 'build',
            'build_server_image': 'build-image'}}, version='1.1')

    def test_10_rejects_all_11_only_flags_without_resource_call(self):
        for extra in (['--image', 'image'], ['--build-server-plan', 'build'],
                      ['--build-server-image', 'image'],
                      ['--worker', 'kind=vm,plan=worker,image=image']):
            before = len(self.http.request_history)
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      stack.CreateStack,
                                      ['demo', '--plan', 'control', *extra])
            self.assertIn('--os-coast-api-microversion 1.1', str(error))
            self.assertEqual(before, len(self.http.request_history))

    def test_11_requires_vm_workers_and_build_flag(self):
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.1')
        for extra in ([], ['--worker', 'kind=baremetal,plan=worker'],
                      ['--worker', 'kind=vm,plan=worker',
                       '--build-server-plan', 'build'],
                      ['--worker', 'kind=vm,plan=worker',
                       '--build-server-image', 'image']):
            before = len(self.http.request_history)
            self.assertRaises(exceptions.CommandError, self.execute,
                              stack.CreateStack,
                              ['demo', '--plan', 'control', *extra])
            self.assertEqual(before, len(self.http.request_history))

    def test_11_accepts_many_vm_workers(self):
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.1')
        self.register('POST', 'stacks', json={'stack': {'id': STACK}},
                      status_code=202)
        self.execute(stack.CreateStack, [
            'demo', '--plan', 'control', '--worker', 'kind=vm,plan=worker',
            '--worker', 'kind=vm,plan=worker'])
        body = self.http.request_history[-1].json()
        self.assertEqual(2, len(body['stack']['workers']))

    def test_stack_show_json_yaml_columns_and_nested_values(self):
        response = {'id': STACK, 'name': 'demo', 'status': 'ACTIVE',
                    'updated_at': None,
                    'links': [{'rel': 'self', 'href': BASE}],
                    'extra': {'nested': ['value']}}
        self.register('GET', STACK_PATH, json={'stack': response})
        for fmt, load in (('json', json.loads), ('yaml', yaml.safe_load)):
            self.app.stdout = io.StringIO()
            self.execute(stack.ShowStack, [STACK, '-f', fmt], formatted=True)
            self.assertEqual(labels.labelled(response),
                             load(self.app.stdout.getvalue()))
        self.app.stdout = io.StringIO()
        self.execute(stack.ShowStack, [STACK, '-f', 'json', '-c', 'id'], True)
        self.assertEqual(labels.labelled({'id': STACK}),
                         json.loads(self.app.stdout.getvalue()))
        self.assert_request('GET', STACK_PATH)

    def test_table_output_renders_returned_links(self):
        response = {'id': STACK, 'status': 'PENDING', 'links': [
            {'rel': 'self', 'href': BASE + STACK_PATH}]}
        for cls, argv, method, path, envelope in (
                CASES[0], CASES[2], CASES[4]):
            self.register(method, path, json={envelope: response})
            self.app.stdout = io.StringIO()
            self.execute(cls, argv, True)
            self.assertIn(BASE + STACK_PATH, self.app.stdout.getvalue())
            self.assertIn('PENDING', self.app.stdout.getvalue())

    def test_all_commands_send_selected_microversion_and_exact_body(self):
        for version in ('1.0', '1.1'):
            self.app.client_manager.coast = client.Client(
                self.session, region_name='region', microversion=version)
            for cls, argv, method, path, envelope in CASES:
                options = list(argv)
                body = None
                if cls is stack.CreateStack:
                    body = {'stack': {'name': 'demo', 'plan': 'control'}}
                    if version == '1.1':
                        options += ['--worker', 'kind=vm,plan=worker']
                        body['stack']['workers'] = [
                            {'kind': 'vm', 'plan': 'worker'}]
                elif cls is server.AddServer:
                    body = {'server': {'name': 'worker', 'kind': 'vm',
                                       'role': 'worker', 'plan': 'worker'}}
                elif method == 'POST':
                    body = {envelope: {'name': 'demo'}}
                response = ([] if envelope.endswith('s')
                            else {'id': STACK, 'status': 'PENDING'})
                self.register(method, path, json={envelope: response})
                self.execute(cls, options)
                self.assert_request(method, path, body, version)

    def test_stack_show_admin_fields_only_when_returned(self):
        for response in (
                {'id': STACK},
                {'id': STACK, 'network_id': 'network', 'engine_ref': 'engine',
                 'task_state': None, 'fault': None}):
            self.register('GET', STACK_PATH, json={'stack': response})
            cols, data = self.execute(stack.ShowStack, [STACK])
            self.assertEqual(labels.labelled(response), dict(zip(cols, data)))

    def test_stack_driver_only_when_present(self):
        for driver in ({}, {'driver': 'coolify_v4'}, {'driver': None}):
            response = {'id': STACK, 'name': 'demo', 'status': 'ACTIVE',
                        'plan': 'standard',
                        'engine_version': '4.4.3', 'created_at': None,
                        **driver}
            self.register('GET', STACK_PATH, json={'stack': response})
            self.register('GET', 'stacks', json={'stacks': [response]})
            expected = {'ID': STACK, 'Name': 'demo', 'Status': 'ACTIVE',
                        'Plan': 'standard',
                        'Engine version': '4.4.3', 'Created at': None}
            if driver:
                expected['Driver'] = driver['driver']
            for cls, argv, label in (
                    (stack.ListStack, [], 'Driver'),
                    (stack.ShowStack, [STACK], 'Driver')):
                for fmt in ('table', 'json'):
                    with self.subTest(driver=driver, cls=cls, fmt=fmt):
                        self.app.stdout = io.StringIO()
                        self.execute(cls, [*argv, '-f', fmt], True)
                        output = self.app.stdout.getvalue()
                        if driver:
                            self.assertIn(label, output)
                        else:
                            self.assertNotIn(label, output)
                        if fmt == 'json':
                            shown = ([expected] if cls is stack.ListStack
                                     else response)
                            self.assertEqual(labels.labelled(shown),
                                             json.loads(output))

    def test_stack_list_driver_on_later_page(self):
        self.register('GET', 'stacks', json={
            'stacks': [{'id': STACK}],
            'links': [{'rel': 'next', 'href': '?marker=' + STACK}]})
        self.register(
            'GET', 'stacks?marker=' + STACK, complete_qs=True,
            json={'stacks': [{'id': SERVER, 'driver': 'coolify_v4'}]})
        columns, rows = self.execute(stack.ListStack, [])
        self.assertEqual(('ID', 'Name', 'Status', 'Plan', 'Driver',
                          'Engine version', 'Created at'), columns)
        rows = list(rows)
        self.assertEqual([STACK, SERVER], [row[0] for row in rows])
        self.assertEqual('coolify_v4', rows[1][columns.index('Driver')])

    def test_empty_stack_list_omits_driver(self):
        self.register('GET', 'stacks', json={'stacks': []})
        columns, rows = self.execute(stack.ListStack, [])
        self.assertEqual(('ID', 'Name', 'Status', 'Plan', 'Engine version',
                          'Created at'),
                         columns)
        self.assertEqual([], list(rows))

    def test_create_and_add_machine_output_do_not_claim_completion(self):
        for cls, argv, method, path, envelope in (CASES[0], CASES[4]):
            response = {'id': SERVER, 'status': 'PENDING', 'links': []}
            self.register(method, path, status_code=202,
                          json={envelope: response})
            before = len(self.http.request_history)
            self.app.stdout = io.StringIO()
            self.execute(cls, [*argv, '-f', 'json'], True)
            self.assertEqual(labels.labelled(response),
                             json.loads(self.app.stdout.getvalue()))
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_stack_delete_retention_body_and_json_response(self):
        response = {'id': STACK, 'status': 'DELETING'}
        self.register('DELETE', STACK_PATH, status_code=202,
                      json={'stack': response})
        self.execute(stack.DeleteStack, [STACK, '--snapshot-volumes',
                                         '--snapshot-retention-days', '0',
                                         '--export-before-delete'])
        self.assert_request('DELETE', STACK_PATH, {
            'snapshot_volumes': True, 'snapshot_retention_days': 0,
            'export_before_delete': True})
        self.assertEqual('', self.app.stdout.getvalue())

    def test_stack_delete_export_alone_and_no_body_defaults(self):
        self.register('DELETE', STACK_PATH, status_code=202, text='')
        self.execute(stack.DeleteStack, [STACK, '--export-before-delete'])
        self.assert_request('DELETE', STACK_PATH,
                            {'export_before_delete': True})
        self.execute(stack.DeleteStack, [STACK])
        self.assert_request('DELETE', STACK_PATH)
        self.assertEqual('', self.app.stdout.getvalue())

    def test_retention_requires_snapshot_without_http(self):
        before = len(self.http.request_history)
        self.assertRaises(exceptions.CommandError, self.execute,
                          stack.DeleteStack,
                          [STACK, '--snapshot-retention-days', '1'])
        self.assertEqual(before, len(self.http.request_history))

    def test_server_add_exact_envelope_and_required_plan(self):
        for kind, role in (('vm', 'worker'), ('vm', 'build'),
                           ('baremetal', 'worker')):
            self.register('POST', SERVERS_PATH, status_code=202,
                          json={'server': {'id': SERVER, 'status': 'PENDING'}})
            self.execute(server.AddServer, ['new', *SCOPED, '--kind', kind,
                                            '--role', role,
                                            '--plan', 'chosen'])
            self.assert_request('POST', SERVERS_PATH, {'server': {
                'name': 'new', 'kind': kind, 'role': role, 'plan': 'chosen'}})
        before = len(self.http.request_history)
        error = self.assertRaises(
            SystemExit, self.execute, server.AddServer,
            ['new', *SCOPED, '--kind', 'vm', '--role', 'worker'])
        self.assertEqual(2, error.code)
        self.assertEqual(before, len(self.http.request_history))

    def test_remove_response_and_empty_response_no_poll(self):
        for response in ({'server': {'id': SERVER, 'status': 'DELETING'}},
                         None):
            kwargs = {'json': response} if response else {'text': ''}
            self.register('DELETE', SERVER_PATH, status_code=202, **kwargs)
            self.app.stdout = io.StringIO()
            before = len(self.http.request_history)
            self.execute(server.RemoveServer, [SERVER, *SCOPED])
            self.assert_request('DELETE', SERVER_PATH)
            output = self.app.stdout.getvalue()
            self.assertEqual('', output)
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_lists_columns_pagination_and_options(self):
        for cls, argv, path, envelope, response, expected in (
                (stack.ListStack, [], 'stacks', 'stacks',
                 {'id': STACK, 'name': 'demo', 'status': 'ACTIVE',
                  'plan': 'standard', 'driver': 'coolify_v4',
                  'engine_version': '4.4.3',
                  'created_at': '2026-10-18T10:00:00Z'},
                 ('ID', 'Name', 'Status', 'Plan', 'Driver', 'Engine version',
                  'Created at')),
                (server.ListServer, SCOPED, SERVERS_PATH, 'servers',
                 {'id': SERVER, 'name': 'worker', 'kind': 'vm',
                  'role': 'worker',
                  'status': 'ACTIVE', 'floating_ip': None,
                  'hostname': 'worker.example.com'},
                 ('ID', 'Name', 'Status', 'Kind', 'Role', 'Floating IP',
                  'Hostname', 'Flavor'))):
            next_url = BASE + path + '?marker=' + SERVER
            self.register('GET', path, json={envelope: [response], 'links': [
                {'rel': 'next', 'href': next_url}]})
            self.register('GET', path + '?marker=' + SERVER,
                          json={envelope: [{'id': SERVER}]},
                          complete_qs=True)
            cols, rows = self.execute(cls, [*argv, '--limit', '1', '--marker',
                                            STACK, '--sort-key', 'name',
                                            '--sort-dir', 'desc'])
            self.assertEqual(expected, cols)
            rows = list(rows)
            self.assertEqual(2, len(rows))
            fields = [('created_at' if col == 'Created at' else
                       col.lower().replace(' ', '_')) for col in cols]
            expected_row = dict(response)
            if cls is server.ListServer:
                rows[0] = tuple(
                    value.machine_readable() if isinstance(
                        value, server.FlavorColumn) else value
                    for value in rows[0])
                expected_row['flavor'] = ''
            self.assertEqual(expected_row, dict(zip(fields, rows[0])))
            self.assertEqual(SERVER, rows[1][0])
            first = self.http.request_history[-2]
            self.assertEqual({'limit': ['1'], 'marker': [STACK],
                              'sort_key': ['name'], 'sort_dir': ['desc']},
                             first.qs)
            self.assertFalse(first.body)
            self.assert_request('GET', path + '?marker=' + SERVER)

    def test_empty_lists_json_and_relative_links(self):
        for cls, argv, method, path, envelope in (CASES[1], CASES[5]):
            self.register('GET', path, json={envelope: [], 'links': [
                {'rel': 'next', 'href': '?marker=' + SERVER}]})
            self.register('GET', path + '?marker=' + SERVER,
                          json={envelope: []}, complete_qs=True)
            self.app.stdout = io.StringIO()
            self.execute(cls, [*argv, '-f', 'json'], True)
            self.assertEqual([], json.loads(self.app.stdout.getvalue()))

    def test_pagination_cannot_leave_collection_or_loop(self):
        for href in ('https://other.example.com/v1/stacks?marker=one',
                     BASE + 'services?marker=one', '/v1/stacks#fragment',
                     BASE + 'stacks'):
            self.register('GET', 'stacks', json={'stacks': [], 'links': [
                {'rel': 'next', 'href': href}]})
            before = len(self.http.request_history)
            self.assertRaises(exceptions.CommandError, self.execute,
                              stack.ListStack, [])
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_invalid_arguments_fail_without_resource_http(self):
        cases = [(stack.CreateStack, []), (stack.CreateStack, ['demo']),
                 (server.AddServer, ['worker']), (server.ListServer, []),
                 (server.RemoveServer, [SERVER]),
                 (server.AddServer, [*ADD, '--kind', 'byo']),
                 (server.AddServer, [*ADD, '--role', 'control']),
                 (stack.ListStack, ['--limit', '0']),
                 (stack.ListStack, ['--marker', 'not-a-uuid']),
                 (stack.DeleteStack,
                  [STACK, '--snapshot-retention-days', '-1']),
                 (stack.CreateStack, ['demo', '--plan', 'c',
                                      '--data-volume-gb', '0'])]
        cases.extend((stack.CreateStack,
                      ['demo', '--plan', 'c', '--worker', w])
                     for w in ('vm', 'kind=vm', 'kind=byo,plan=x',
                               'kind=vm,plan=', 'kind=vm,plan=x,plan=y',
                               'kind=vm,plan=x,unknown=y'))
        before = len(self.http.request_history)
        for cls, argv in cases:
            self.assertRaises(SystemExit, self.execute, cls, argv)
        self.assertEqual(before, len(self.http.request_history))

    def test_shell_json_output_and_clean_nonzero_http_errors(self):
        auth = ['--os-auth-type', 'v3token', '--os-auth-url',
                'https://identity.example.com/v3', '--os-token', 'input-token',
                '--os-project-id', 'project', '--os-region-name', 'region']
        for status in (200, 404, 409, 503):
            logs = self.useFixture(fixtures.FakeLogger(name='run', level=10))
            app = shell.OpenStackShell()
            app.stdout = io.StringIO()
            app.stderr = io.StringIO()
            response = ({'stack': {'id': STACK, 'status': 'ACTIVE'}}
                        if status == 200 else {'faultstring': 'remote-secret'})
            self.register('GET', STACK_PATH, status_code=status, json=response)
            result = app.run([*auth, 'coast', 'stack', 'show', STACK,
                              '-f', 'json'])
            if status == 200:
                self.assertEqual(0, result)
                self.assertEqual(labels.labelled(response['stack']),
                                 json.loads(app.stdout.getvalue()))
            else:
                self.assertNotEqual(0, result)
                self.assertIn(
                    'not found' if status == 404 else 'HTTP ' + str(status),
                    logs.output)
                self.assertNotIn('remote-secret', logs.output)
                self.assertEqual('', app.stdout.getvalue())

    def test_transport_and_invalid_json_fail_without_remote_text(self):
        for outcome in ({'exc': requests.exceptions.ConnectionError},
                        {'status_code': 200, 'text': 'remote-secret'}):
            self.register('POST', 'stacks', **outcome)
            before = len(self.http.request_history)
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      stack.CreateStack,
                                      ['demo', '--plan', 'control'])
            self.assertNotIn('remote-secret', str(error))
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_child_create_description_and_omission_no_wait(self):
        for cls, argv, method, path, envelope in CHILD_CASES[::4]:
            response = {'id': APPGROUP, 'name': 'demo',
                        'description': None, 'status': 'PENDING',
                        'links': [{'rel': 'self', 'href': BASE + path}]}
            self.register(method, path, status_code=202,
                          json={envelope: response})
            for description in (None, '', 'description'):
                options = (argv if description is None else
                           [*argv, '--description', description])
                body = {'name': 'demo'}
                if description is not None:
                    body['description'] = description
                self.app.stdout = io.StringIO()
                before = len(self.http.request_history)
                self.execute(cls, [*options, '-f', 'json'], True)
                self.assert_request(method, path, {envelope: body})
                self.assertEqual(labels.labelled(response),
                                 json.loads(self.app.stdout.getvalue()))
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_child_show_wire_fields_formatters_and_columns(self):
        for cls, argv, method, path, envelope in CHILD_CASES[2::4]:
            parent = ('stack_id' if envelope == 'appgroup'
                      else 'appgroup_id')
            response = {'id': argv[0], parent: STACK,
                        'description': None, 'status': 'ACTIVE',
                        'links': [{'rel': 'self', 'href': BASE + path}]}
            self.register(method, path, json={envelope: response})
            response.update({'apps': [], 'services': []})
            if envelope == 'appgroup':
                response['environments'] = []
            for fmt, load in (('json', json.loads), ('yaml', yaml.safe_load)):
                self.app.stdout = io.StringIO()
                self.execute(cls, [*argv, '-f', fmt], True)
                self.assertEqual(labels.labelled(response),
                                 load(self.app.stdout.getvalue()))
                self.assert_request(method, path)
            self.app.stdout = io.StringIO()
            self.execute(cls, [*argv, '-f', 'json', '-c', 'id'], True)
            self.assertEqual(labels.labelled({'id': argv[0]}),
                             json.loads(self.app.stdout.getvalue()))

    def test_child_delete_json_or_empty_no_body_no_wait(self):
        for cls, argv, method, path, envelope in CHILD_CASES[3::4]:
            for response in ({'id': argv[0], 'status': 'DELETING'}, None):
                kwargs = ({'json': {envelope: response}} if response else
                          {'text': ''})
                self.register(method, path, status_code=202, **kwargs)
                self.app.stdout = io.StringIO()
                before = len(self.http.request_history)
                self.execute(cls, argv)
                self.assert_request(method, path)
                self.assertEqual('', self.app.stdout.getvalue())
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_child_list_columns_parent_scope_and_pagination(self):
        for cls, argv, method, path, envelope in CHILD_CASES[1::4]:
            parent = ('stack_id' if envelope == 'appgroups'
                      else 'appgroup_id')
            parent_id = STACK if envelope == 'appgroups' else APPGROUP
            response = {'id': ENVIRONMENT, 'name': 'demo',
                        parent: parent_id, 'status': 'ACTIVE'}
            self.register(method, path, json={envelope: [response], 'links': [
                {'rel': 'next', 'href': '?marker=' + ENVIRONMENT}]})
            self.register(method, path + '?marker=' + ENVIRONMENT,
                          json={envelope: [{'id': APPGROUP}]},
                          complete_qs=True)
            cols, rows = self.execute(cls, [*argv, '--limit', '1', '--marker',
                                            STACK, '--sort-key', 'name',
                                            '--sort-dir', 'desc'])
            parent_column = ('Stack ID' if envelope == 'appgroups'
                             else 'App group ID')
            self.assertEqual(('ID', 'Name', 'Status', parent_column), cols)
            rows = list(rows)
            self.assertEqual((ENVIRONMENT, 'demo', 'ACTIVE', parent_id),
                             rows[0])
            self.assertEqual((APPGROUP, '', '', ''), rows[1])
            first = self.http.request_history[-2]
            self.assertEqual({'limit': ['1'], 'marker': [STACK],
                              'sort_key': ['name'], 'sort_dir': ['desc']},
                             first.qs)
            self.assertFalse(first.body)
            self.assert_request(method, path + '?marker=' + ENVIRONMENT)

    def test_child_list_empty_and_machine_output(self):
        for cls, argv, method, path, envelope in CHILD_CASES[1::4]:
            parent = ('Stack ID' if envelope == 'appgroups'
                      else 'App group ID')
            for rows in ([], [{'id': APPGROUP, 'name': 'demo',
                               ('stack_id' if envelope == 'appgroups'
                                else 'appgroup_id'): STACK,
                               'status': 'ACTIVE'}]):
                self.register(method, path, json={envelope: rows})
                self.app.stdout = io.StringIO()
                self.execute(cls, [*argv, '-f', 'json'], True)
                expected = ([{'ID': APPGROUP, 'Name': 'demo', parent: STACK,
                              'Status': 'ACTIVE'}] if rows else [])
                self.assertEqual(labels.labelled(expected),
                                 json.loads(self.app.stdout.getvalue()))
                self.assert_request(method, path)

    def test_child_pagination_cannot_change_parent_or_collection(self):
        for cls, argv, method, path, envelope in CHILD_CASES[1::4]:
            for href in (BASE + path.replace(STACK, ENVIRONMENT).replace(
                    APPGROUP, ENVIRONMENT), BASE + APPGROUP_PATH,
                    'https://other.example.com/v1/' + path, BASE + path):
                self.register(method, path, json={envelope: [], 'links': [
                    {'rel': 'next', 'href': href}]})
                before = len(self.http.request_history)
                self.assertRaises(exceptions.CommandError, self.execute,
                                  cls, argv)
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_child_invalid_arguments_fail_before_http(self):
        for cls, argv, method, path, envelope in CHILD_CASES:
            invalid = [[], [*argv, '--wait']]
            if method == 'POST':
                invalid += [argv[1:], ['demo'],
                            [argv[0], argv[1], '--unknown']]
            elif envelope.endswith('s'):
                invalid += [[*argv, '--limit', '0'],
                            [*argv, '--marker', 'name'],
                            [*argv, '--sort-dir', 'sideways'],
                            [*argv, '--unknown-filter', 'demo']]
            else:
                invalid += [[*argv, '--stack', STACK]]
                if envelope == 'appgroup':
                    invalid += [[*argv, '--appgroup', APPGROUP]]
                if method == 'DELETE':
                    invalid += [[*argv, '--cascade'], [*argv, '-f', 'json']]
            for options in invalid:
                before = len(self.http.request_history)
                self.assertRaises(SystemExit, self.execute, cls, options)
                self.assertEqual(before, len(self.http.request_history))

    def test_child_transport_invalid_json_and_uncertain_write_no_retry(self):
        for cls, argv, method, path, envelope in CHILD_CASES:
            for outcome in ({'exc': requests.exceptions.ConnectionError},
                            {'status_code': 200, 'text': 'remote-secret'}):
                self.register(method, path, **outcome)
                before = len(self.http.request_history)
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls, argv)
                self.assertNotIn('remote-secret', str(error))
                self.assertEqual(1, len(self.http.request_history) - before)

    def test_child_shell_success_and_nonzero_isolation_conflict_errors(self):
        auth = ['--os-auth-type', 'v3token', '--os-auth-url',
                'https://identity.example.com/v3', '--os-token', 'input-token',
                '--os-project-id', 'project', '--os-region-name', 'region']
        for cls, argv, method, path, envelope in CHILD_CASES:
            resource = envelope.rstrip('s')
            verb = cls.__name__.removesuffix(resource.title()).lower()
            for status in (202 if method != 'GET' else 200, 404, 409):
                logs = self.useFixture(fixtures.FakeLogger(
                    name='run', level=10))
                app = shell.OpenStackShell()
                app.stdout = io.StringIO()
                app.stderr = io.StringIO()
                response = ([] if verb == 'list' else
                            {'id': APPGROUP, 'status': 'PENDING'})
                self.register(method, path, status_code=status, json=(
                    {envelope: response} if status < 300 else
                    {'faultstring': 'remote-secret'}))
                options = [] if verb == 'delete' else ['-f', 'json']
                result = app.run([*auth, 'coast', resource, verb,
                                  *argv, *options])
                if status < 300:
                    self.assertEqual(0, result)
                    if verb == 'show':
                        response.update({'apps': [], 'services': []})
                        if resource == 'appgroup':
                            response['environments'] = []
                    if verb == 'delete':
                        self.assertEqual('', app.stdout.getvalue())
                    else:
                        self.assertEqual(labels.labelled(response),
                                         json.loads(app.stdout.getvalue()))
                else:
                    self.assertNotEqual(0, result)
                    self.assertIn(
                        'not found' if status == 404 else
                        'HTTP ' + str(status),
                        logs.output)
                    self.assertNotIn('remote-secret', logs.output)
                    self.assertEqual('', app.stdout.getvalue())

    def test_parent_lookup_pagination_uuid_and_errors(self):
        for resource in ('appgroup', 'environment'):
            self.register('GET', 'stacks',
                          json={'stacks': [{'id': STACK}]})
            self.register('GET', APPGROUPS_PATH,
                          json={'appgroups': [{'id': APPGROUP}]})
            path = (APPGROUPS_PATH if resource == 'appgroup'
                    else ENVIRONMENTS_PATH)
            envelope = resource + 's'
            self.register('GET', path, json={envelope: [], 'links': [
                {'rel': 'next', 'href': '?marker=next'}]})
            self.register('GET', path + '?marker=next', complete_qs=True,
                          json={envelope: [{'id': APPGROUP,
                                            'name': 'demo'}]})
            self.assertEqual(APPGROUP, utils.find_resource(
                self.api, 'demo', resource))
            before = len(self.http.request_history)
            self.assertEqual(APPGROUP, utils.find_resource(
                self.api, APPGROUP.replace('-', ''), resource))
            self.assertEqual(before, len(self.http.request_history))
            for rows, message in (([], 'not found.'),
                                  ([{'name': 'demo'}, {'name': 'demo'}],
                                   'More than one ')):
                self.register('GET', path, json={envelope: rows},
                              complete_qs=True)
                error = self.assertRaises(exceptions.CommandError,
                                          utils.find_resource,
                                          self.api, 'demo', resource)
                self.assertIn(message, str(error))

    def test_parent_lookup_checks_all_stacks_and_appgroups(self):
        self.register('GET', 'stacks', json={'stacks': [{'id': STACK}],
                      'links': [{'rel': 'next', 'href': '?marker=' + STACK}]})
        self.register('GET', 'stacks?marker=' + STACK, complete_qs=True,
                      json={'stacks': [{'id': SERVER}]})
        for stack_id, group_id in ((STACK, APPGROUP), (SERVER, ENVIRONMENT)):
            self.register('GET', 'stacks/' + stack_id + '/appgroups',
                          json={'appgroups': [{'id': group_id,
                                               'name': 'duplicate'}]})
            self.register('GET', 'appgroups/' + group_id + '/environments',
                          json={'environments': [{'id': group_id,
                                                  'name': 'duplicate'}]})
        for resource in ('appgroup', 'environment'):
            error = self.assertRaises(exceptions.CommandError,
                                      utils.find_resource, self.api,
                                      'duplicate', resource)
            self.assertIn('More than one ', str(error))
        self.register('GET', 'appgroups/' + ENVIRONMENT + '/environments',
                      json={'environments': [{'id': ENVIRONMENT,
                                              'name': 'unique'}]})
        self.assertEqual(ENVIRONMENT, utils.find_resource(
            self.api, 'unique', 'environment'))

    def test_environment_set_validates_appgroup_ownership(self):
        self.register('GET', APPGROUP_PATH,
                      json={'appgroup': {'id': APPGROUP}})
        self.register('GET', ENVIRONMENTS_PATH, json={'environments': [
            {'id': ENVIRONMENT, 'name': 'production'}]})
        self.register('GET', ENVIRONMENT_PATH, json={'environment': {
            'id': ENVIRONMENT, 'appgroup_id': APPGROUP}})
        self.register('PATCH', ENVIRONMENT_PATH, json={'environment': {
            'id': ENVIRONMENT, 'name': 'renamed'}})
        for reference in ('production', ENVIRONMENT):
            with self.subTest(reference=reference):
                self.execute(catchup.EnvironmentSet, [
                    reference, '--appgroup', APPGROUP, '--name', 'renamed'])
                self.assert_request(
                    'PATCH', ENVIRONMENT_PATH,
                    {'environment': {'name': 'renamed'}})
        self.register('GET', ENVIRONMENT_PATH, json={'environment': {
            'id': ENVIRONMENT, 'appgroup_id': SERVER}})
        before = len(self.http.request_history)
        error = self.assertRaises(
            exceptions.CommandError, self.execute, catchup.EnvironmentSet,
            [ENVIRONMENT, '--appgroup', APPGROUP, '--name', 'renamed'])
        self.assertIn('not found in app group', str(error))
        self.assertFalse(any(request.method == 'PATCH' for request in
                             self.http.request_history[before:]))

    def test_environment_names_scoped_to_appgroup(self):
        self.register('GET', 'stacks', json={'stacks': [{'id': STACK}]})
        self.register('GET', APPGROUPS_PATH, json={'appgroups': [
            {'id': APPGROUP, 'name': 'qa'},
            {'id': SERVER, 'name': 'burger'}]})
        for group_id, env_id in ((APPGROUP, ENVIRONMENT), (SERVER, STACK)):
            self.register('GET', 'appgroups/' + group_id + '/environments',
                          json={'environments': [
                              {'id': env_id, 'name': 'production',
                               'appgroup_id': group_id}]})
        error = self.assertRaises(exceptions.CommandError,
                                  utils.find_resource, self.api,
                                  'production', 'environment')
        self.assertEqual(
            "More than one environment is named 'production'; use the ID.",
            str(error))
        self.register('GET', ENVIRONMENT_PATH,
                      json={'environment': {'id': ENVIRONMENT,
                                            'appgroup_id': APPGROUP}})
        self.register('DELETE', ENVIRONMENT_PATH, status_code=204)
        self.register('PATCH', ENVIRONMENT_PATH, json={'environment': {
            'id': ENVIRONMENT, 'name': 'renamed'}})
        self.register('GET', 'services', json={'services': []})
        self.register('GET', APPGROUP_PATH,
                      json={'appgroup': {'id': APPGROUP}})
        for scope in ('qa', APPGROUP):
            for cls in (service.ListApp, service.ListService,
                        environment.ShowEnvironment,
                        environment.DeleteEnvironment,
                        catchup.EnvironmentSet):
                before = len(self.http.request_history)
                if cls in (service.ListApp, service.ListService):
                    cols, rows = self.execute(cls, [
                        '--appgroup', scope, '--environment', 'production'])
                    self.assertEqual([], list(rows))
                    self.assertEqual({'appgroup_id': [APPGROUP],
                                      'environment_id': [ENVIRONMENT],
                                      'kind': [cls.kind]},
                                     self.http.last_request.qs)
                else:
                    options = ['production', '--appgroup', scope]
                    if cls is catchup.EnvironmentSet:
                        options += ['--name', 'renamed']
                    self.execute(cls, options)
                    method = ('GET' if cls is environment.ShowEnvironment
                              else 'PATCH' if cls is catchup.EnvironmentSet
                              else 'DELETE')
                    body = ({'environment': {'name': 'renamed'}}
                            if cls is catchup.EnvironmentSet else None)
                    self.assert_request(method, ENVIRONMENT_PATH, body)
                paths = [req.url.split('?')[0] for req in
                         self.http.request_history[before:]]
                self.assertNotIn(
                    BASE + 'appgroups/' + SERVER + '/environments', paths)
                if scope == APPGROUP:
                    self.assertNotIn(BASE + 'stacks', paths)
        self.assertEqual(ENVIRONMENT, utils.find_resource(
            self.api, ENVIRONMENT, 'environment', appgroup_id=APPGROUP))
        self.assertRaises(exceptions.CommandError, utils.find_resource,
                          self.api, 'missing', 'environment',
                          appgroup_id=APPGROUP)

    def test_parent_show_delete_names_and_live_children(self):
        for resource, show, delete, identity in (
                ('appgroup', appgroup.ShowAppgroup, appgroup.DeleteAppgroup,
                 APPGROUP),
                ('environment', environment.ShowEnvironment,
                 environment.DeleteEnvironment, ENVIRONMENT)):
            self.register('GET', 'stacks',
                          json={'stacks': [{'id': STACK}]})
            self.register('GET', APPGROUPS_PATH,
                          json={'appgroups': [{'id': APPGROUP}]})
            collection = (APPGROUPS_PATH if resource == 'appgroup'
                          else ENVIRONMENTS_PATH)
            self.register('GET', collection, complete_qs=True,
                          json={resource + 's': [{'id': identity,
                                                 'name': 'demo'}]})
            self.register('GET', resource + 's/' + identity,
                          json={resource: {'id': identity}})
            self.register('DELETE', resource + 's/' + identity,
                          status_code=204)
            for status in ('ACTIVE', 'STOPPED'):
                rows = [{'id': 'app-id', 'name': 'web', 'status': status,
                         'kind': 'app'},
                        {'id': 'service-id', 'name': 'db', 'status': status,
                         'kind': 'service'}]
                self.register(
                    'GET', 'services?' + resource + '_id=' + identity,
                    complete_qs=True, json={'services': rows[:1], 'links': [
                        {'rel': 'next', 'href': '?' + resource + '_id=' +
                         identity + '&marker=next'}]})
                self.register(
                    'GET', 'services?' + resource + '_id=' + identity +
                    '&marker=next', complete_qs=True,
                    json={'services': rows[1:]})
                if resource == 'appgroup':
                    self.register(
                        'GET', 'appgroups/' + identity + '/environments',
                        complete_qs=True, json={'environments': [
                            {'id': 'env-id', 'name': 'prod',
                             'status': status}]})
                self.app.stdout = io.StringIO()
                self.execute(show, ['demo', '-f', 'json'], True)
                data = json.loads(self.app.stdout.getvalue())
                self.assertEqual(['web (%s) app-id' % status], data['Apps'])
                self.assertEqual(['db (%s) service-id' % status],
                                 data['Services'])
                if resource == 'appgroup':
                    self.assertEqual(['prod (%s) env-id' % status],
                                     data['Environments'])
            self.execute(delete, ['demo'])
            self.assert_request('DELETE', resource + 's/' + identity)

    def test_workload_list_resolves_both_parent_filters(self):
        for cls in (service.ListApp, service.ListService):
            self.register('GET', 'stacks',
                          json={'stacks': [{'id': STACK}]})
            for resource in ('appgroup', 'environment'):
                path = (APPGROUPS_PATH if resource == 'appgroup' else
                        'appgroups/' + APPGROUP + '/environments')
                self.register('GET', path, json={resource + 's': [
                    {'id': APPGROUP, 'name': 'demo',
                     'appgroup_id': APPGROUP}]})
            self.register('GET', 'services', json={'services': []})
            cols, rows = self.execute(cls, ['--appgroup', 'demo',
                                            '--environment', 'demo'])
            self.assertEqual([], list(rows))
            self.assertEqual({'appgroup_id': [APPGROUP],
                              'environment_id': [APPGROUP],
                              'kind': [cls.kind]}, self.http.last_request.qs)

    def event_api(self):
        self.http.get(ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.8', 'links': [{'rel': 'self', 'href': BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.8')
        self.app.client_manager.coast = self.api

    def test_stack_events_pages_order_and_headers(self):
        self.event_api()
        path = STACK_PATH + '/events'
        event = {'created_at': '2026-10-18T12:00:00Z',
                 'resource_type': 'server', 'resource_id': SERVER,
                 'action': 'create', 'outcome': 'success', 'message': 'Ready'}
        self.register('GET', path, json={'events': [event], 'links': [
            {'rel': 'next', 'href': '?marker=' + SERVER}]})
        self.register('GET', path + '?marker=' + SERVER,
                      json={'events': [dict(event, action='start')]},
                      complete_qs=True)
        cols, rows = self.execute(stack.ListStackEvent, [STACK])
        self.assertEqual(('Created at', 'Resource type', 'Resource ID',
                          'Action',
                          'Outcome', 'Message'), cols)
        rows = list(rows)
        self.assertEqual([event[key] for key in (
            'created_at', 'resource_type', 'resource_id', 'action',
            'outcome', 'message')], list(rows[0]))
        self.assertEqual('start', rows[1][3])
        self.assert_request('GET', path + '?marker=' + SERVER, version='1.8')

    def test_stack_events_limit_filters_and_json_detail(self):
        self.event_api()
        path = STACK_PATH + '/events'
        self.register('GET', path, json={'events': [
            {'id': SERVER, 'detail': {'step': 'boot', 'attempt': 2}},
            {'id': STACK, 'detail': None}],
            'links': [{'rel': 'next', 'href': '?marker=' + STACK}]})
        before = len(self.http.request_history)
        cols, rows = self.execute(stack.ListStackEvent, [
            STACK, '--limit', '2', '--marker', SERVER,
            '--resource-type', 'server', '--long'])
        self.assertEqual('Detail', cols[-1])
        self.assertEqual('ID', cols[0])
        rows = list(rows)
        self.assertEqual([SERVER, STACK], [row[0] for row in rows])
        self.assertEqual({'attempt': 2, 'step': 'boot'},
                         json.loads(rows[0][-1]))
        self.assertEqual('null', rows[1][-1])
        self.assertEqual(1, len(self.http.request_history) - before)
        self.assertEqual({'limit': ['2'], 'marker': [SERVER],
                          'resource_type': ['server']},
                         self.http.last_request.qs)

    def test_stack_events_resolve_name_across_pages(self):
        self.event_api()
        self.register('GET', 'stacks', json={'stacks': [], 'links': [
            {'rel': 'next', 'href': '?marker=' + SERVER}]})
        self.register('GET', 'stacks?marker=' + SERVER, complete_qs=True,
                      json={'stacks': [{'id': STACK, 'name': 'demo'}]})
        self.register('GET', STACK_PATH + '/events', json={'events': []})
        _, rows = self.execute(stack.ListStackEvent, ['demo'])
        self.assertEqual([], list(rows))
        self.assert_request('GET', STACK_PATH + '/events?limit=50',
                            version='1.8')

    def test_stack_events_name_errors(self):
        self.event_api()
        for rows, message in (([], 'Stack'),
                              ([{'id': STACK, 'name': 'demo'}] * 2,
                               'More than one stack')):
            self.register('GET', 'stacks', json={'stacks': rows})
            error = self.assertRaises(exceptions.CommandError,
                                      self.execute, stack.ListStackEvent,
                                      ['demo'])
            self.assertIn(message, str(error))

    def test_stack_events_older_selection(self):
        for version in ('1.0', '1.1', '1.7'):
            self.api.microversion = version
            before = len(self.http.request_history)
            error = self.assertRaises(exceptions.CommandError,
                                      self.execute, stack.ListStackEvent,
                                      [STACK])
            self.assertEqual('Stack events need Coast API 1.8 or later.',
                             str(error))
            self.assertEqual(before, len(self.http.request_history))

    def test_stack_events_http_errors_and_unsafe_links(self):
        self.event_api()
        path = STACK_PATH + '/events'
        for status in (401, 403, 404, 406, 503):
            self.register('GET', path, status_code=status,
                          json={'error': {'message': 'private details'}})
            error = self.assertRaises(exceptions.CommandError,
                                      self.execute, stack.ListStackEvent,
                                      [STACK])
            self.assertIn(
                'not found' if status == 404 else 'HTTP %s' % status,
                str(error))
            self.assertNotIn('private details', str(error))
        for link in ('https://other.example.com/events', '?marker=' + SERVER):
            self.register('GET', path, json={'events': [], 'links': [
                {'rel': 'next', 'href': link}]})
            self.assertRaises(exceptions.CommandError, self.execute,
                              stack.ListStackEvent, [STACK])

    def test_stack_events_older_server_discovery_error(self):
        class Manager:
            @property
            def coast(self):
                raise ks_exc.DiscoveryFailure(
                    'Selected app-hosting microversion is outside '
                    'discovery bounds')

        self.app.client_manager = Manager()
        error = self.assertRaises(exceptions.CommandError, self.execute,
                                  stack.ListStackEvent, [STACK])
        self.assertEqual('Stack events need Coast API 1.8 or later.',
                         str(error))

    def test_stack_events_invalid_options(self):
        for options in (['--limit', '0'], ['--limit', '-1'],
                        ['--marker', 'invalid'], ['--resource-type', 'vm']):
            self.assertRaises(SystemExit, self.execute, stack.ListStackEvent,
                              [STACK, *options])

    def test_stack_created_at_show(self):
        self.register('GET', STACK_PATH, json={'stack': {
            'id': STACK, 'created_at': '2026-10-18T12:00:00Z'}})
        cols, data = self.execute(stack.ShowStack, [STACK])
        self.assertEqual('2026-10-18T12:00:00Z',
                         dict(zip(cols, data))[utils.label('created_at')])

    def test_event_defaults_and_total_limits(self):
        self.event_api()
        for cls, argv, path in (
                (catchup.EventList, [], 'events'),
                (stack.ListStackEvent, [STACK], STACK_PATH + '/events')):
            for limit in (None, 3, 60):
                with self.subTest(command=cls, limit=limit):
                    count = limit or 50
                    first = [{'id': str(i)} for i in range(2)]
                    second = [{'id': str(i)} for i in range(2, 72)]
                    self.register('GET', path, json={
                        'events': first, 'links': [
                            {'rel': 'next', 'href': '?marker=' + SERVER}]})
                    self.register('GET', path + '?marker=' + SERVER,
                                  json={'events': second}, complete_qs=True)
                    before = len(self.http.request_history)
                    options = [] if limit is None else ['--limit', str(limit)]
                    cols, rows = self.execute(cls, argv + options + ['--long'])
                    rows = list(rows)
                    self.assertEqual(count, len(rows))
                    self.assertEqual(['0', '1', '2'],
                                     [row[0] for row in rows[:3]])
                    self.assertIn('Stack ID', cols)
                    self.assertIn('Request ID', cols)
                    query = self.http.request_history[before].qs
                    self.assertEqual([str(count)], query['limit'])
                    if cls is catchup.EventList:
                        self.assertEqual(['created_at'], query['sort_key'])
                        self.assertEqual(['desc'], query['sort_dir'])
                    else:
                        self.assertNotIn('sort_dir', query)

    def test_project_event_request_and_outcome_filters(self):
        self.register('GET', 'events', json={'events': []})
        self.execute(catchup.EventList, [
            '--request-id', 'req-example', '--outcome', 'failure',
            '--marker', SERVER])
        self.assertEqual(['req-example'], self.http.last_request.qs[
            'request_id'])
        self.assertEqual(['failure'], self.http.last_request.qs['outcome'])
        self.assertEqual([SERVER], self.http.last_request.qs['marker'])

    def test_event_outcome_choices(self):
        for cls, argv in ((catchup.EventList, []),
                          (stack.ListStackEvent, [STACK])):
            for value in ('start', 'success', 'failure'):
                parser = cls(self.app, None).get_parser('test')
                self.assertEqual(value, parser.parse_args(
                    argv + ['--outcome', value]).outcome)
            self.assertRaises(SystemExit, parser.parse_args,
                              argv + ['--outcome', 'bogus'])

    def test_stack_outcome_filter_is_sent_from_api_1_8(self):
        for version in ('1.8', '1.28'):
            self.event_api()
            self.api.microversion = version
            path = STACK_PATH + '/events'
            self.register('GET', path, json={
                'events': [{'outcome': 'failure'}],
                'links': [{'rel': 'next', 'href':
                           '?outcome=failure&limit=2&marker=' + SERVER}]})
            self.register('GET', path + '?outcome=failure&limit=2&marker=' +
                          SERVER, json={'events': [{'outcome': 'failure'}]},
                          complete_qs=True)
            _, rows = self.execute(stack.ListStackEvent, [
                STACK, '--outcome', 'failure', '--limit', '2'])
            self.assertEqual(['failure', 'failure'], [row[4] for row in rows])
            requests = [req for req in self.http.request_history
                        if req.path.endswith('/events')]
            for request in requests:
                self.assertEqual(['failure'], request.qs['outcome'])

    def test_stack_event_resource_type_microversions(self):
        for kind in ('app', 'service'):
            self.event_api()
            error = self.assertRaises(
                exceptions.CommandError, self.execute, stack.ListStackEvent,
                [STACK, '--resource-type', kind])
            self.assertEqual(
                'App and service event filters need Coast API 1.9 or later.',
                str(error))
        self.http.get(ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.9', 'links': [{'rel': 'self', 'href': BASE}]}]})
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.9')
        for kind in ('stack', 'server', 'app', 'service'):
            self.register('GET', STACK_PATH + '/events', json={'events': []})
            self.execute(stack.ListStackEvent,
                         [STACK, '--resource-type', kind])
            self.assertEqual([kind],
                             self.http.last_request.qs['resource_type'])


def error_test(cls, argv, method, path, status):
    def test(self):
        logs = self.useFixture(fixtures.FakeLogger(
            name='keystoneauth.session', level=10))
        self.register(method, path, status_code=status, json={
            'faultstring': 'remote-secret',
            'error': {'message': 'remote-secret'}})
        before = len(self.http.request_history)
        error = self.assertRaises(exceptions.CommandError, self.execute,
                                  cls, argv)
        self.assertIn(
            'not found' if status == 404 else 'HTTP ' + str(status),
            str(error))
        if status in (404, 409, 503):
            self.assertIn({404: 'not found', 409: 'Conflict',
                           503: 'Service Unavailable'}[status], str(error))
        self.assertNotIn('remote-secret', str(error))
        self.assertNotIn('remote-secret', logs.output)
        self.assertEqual('', self.app.stdout.getvalue())
        # OSC's session refreshes authentication once after a known 401.
        self.assertEqual(3 if status == 401 else 1,
                         len(self.http.request_history) - before)
    return test


for _cls, _argv, _method, _path, _envelope in CASES:
    for _status in (400, 401, 403, 404, 406, 409, 500, 503):
        setattr(CommandTest, 'test_%s_http_%s' % (_cls.__name__, _status),
                error_test(_cls, _argv, _method, _path, _status))
