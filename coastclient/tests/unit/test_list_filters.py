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

"""M5 list filters reach the API and reject invalid choices offline."""
import contextlib
import io

from oslotest import base

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import deployment
from coastclient.osc.v1 import domain
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import filters
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.tests.unit import test_commands as fixtures


CASES = (
    (stack.ListStack, 'stack', [], 'stacks', 'stacks'),
    (server.ListServer, 'server', fixtures.SCOPED,
     fixtures.SERVERS_PATH, 'servers'),
    (appgroup.ListAppgroup, 'appgroup', fixtures.SCOPED,
     fixtures.APPGROUPS_PATH, 'appgroups'),
    (environment.ListEnvironment, 'environment',
     ['--appgroup', fixtures.APPGROUP], fixtures.ENVIRONMENTS_PATH,
     'environments'),
    (service.ListApp, 'service', [], 'services', 'services'),
    (service.ListService, 'service', [], 'services', 'services'),
    (deployment.ListDeployment, 'deployment', ['--service', fixtures.SERVER],
     'services/' + fixtures.SERVER + '/deployments', 'deployments'),
    (domain.ListDomain, 'domain', ['--service', fixtures.SERVER],
     'services/' + fixtures.SERVER + '/domains', 'domains'),
    (catchup.VolumeList, 'volume', fixtures.SCOPED,
     fixtures.STACK_PATH + '/volumes', 'volumes'),
    (catchup.StorageList, 'storage', fixtures.SCOPED,
     fixtures.STACK_PATH + '/storages', 'storages'),
    (catchup.ExportList, 'export', fixtures.SCOPED,
     fixtures.STACK_PATH + '/exports', 'exports'),
    (catchup.EventList, 'event', [], 'events', 'events'),
)


class ListFiltersTest(base.BaseTestCase):
    set_up_api = fixtures.CommandTest.set_up_api
    register = fixtures.CommandTest.register
    execute = fixtures.CommandTest.execute
    assert_no_top_level_parent_lists = (
        fixtures.CommandTest.assert_no_top_level_parent_lists)

    def setUp(self):
        super().setUp()
        self.set_up_api()

    def test_exact_id_filter(self):
        for cls, resource, parents, path, envelope in CASES:
            with self.subTest(command=cls):
                parser = cls(self.app, None).get_parser('test')
                action = parser._option_string_actions['--id']
                self.assertEqual('ID', action.metavar)
                self.assertEqual('Filter by exact ID.', action.help)
                self.register('GET', path, json={envelope: []})
                self.execute(cls, parents + ['--id', fixtures.SERVER])
                self.assertEqual([fixtures.SERVER],
                                 self.http.last_request.qs['id'])

    def test_filter_help_labels_and_units(self):
        expected = {
            '--memory-limit-mb': 'memory limit in MiB',
            '--floating-ip': 'floating IP',
            '--size-gb': 'size in GiB',
            '--cpu-limit': 'CPU limit',
            '--auto-dns': 'automatic DNS',
            '--health-check-path': 'HTTP probe path',
        }
        for cls, resource, parents, path, envelope in CASES:
            parser = cls(self.app, None).get_parser('test')
            for action in parser._actions:
                for option in action.option_strings:
                    if option in expected:
                        with self.subTest(command=cls, option=option):
                            self.assertIn(expected[option], action.help)

    def test_each_option_sends_its_query_parameter(self):
        for cls, resource, parents, path, envelope in CASES:
            self.register('GET', path, json={envelope: []})
            for field in filters.FIELDS[resource]:
                option = field.removesuffix('_id') if field in (
                    'stack_id', 'server_id') else field
                option = ('health-check-path' if field == 'healthcheck_path'
                          else option.replace('_', '-'))
                choices = (filters.STATUSES[resource] if field == 'status'
                           else filters.CHOICES.get(field))
                value = (choices[0] if choices else fixtures.SERVER
                         if field.endswith('_id') else '2'
                         if field in ('number', 'replicas', 'memory_limit_mb',
                                      'size_gb', 'health_check_port')
                         else '1.5' if field == 'cpu_limit' else 'example')
                boolean = field in ('auto_dns', 'auto_deploy', 'read_only')
                variants = (('', 'true'), ('no-', 'false')) if boolean else (
                    ('', value),)
                for prefix, expected in variants:
                    with self.subTest(command=cls, field=field,
                                      value=expected):
                        argv = parents + ['--' + prefix + option]
                        if not boolean:
                            argv += [value]
                        self.execute(cls, argv)
                        self.assertEqual([expected.lower()],
                                         self.http.last_request.qs[field])

    def test_invalid_choices_exit_two(self):
        for cls, resource, parents, _path, _envelope in CASES:
            for field in filters.FIELDS[resource]:
                if field != 'status' and field not in filters.CHOICES:
                    continue
                with self.subTest(command=cls, field=field):
                    cmd = cls(self.app, None)
                    with contextlib.redirect_stderr(io.StringIO()):
                        error = self.assertRaises(
                            SystemExit, cmd.get_parser('test').parse_args,
                            parents + ['--' + field.replace('_', '-'),
                                       'invalid'])
                    self.assertEqual(2, error.code)

    def test_workload_probe_filters_use_api_keys(self):
        self.register('GET', 'services', json={'services': []})
        for cls in (service.ListApp, service.ListService):
            for mode in ('none', 'http'):
                for port in ('1', '65535'):
                    with self.subTest(command=cls, mode=mode, port=port):
                        self.execute(cls, [
                            '--cpu-limit', '0.5', '--health-check', mode,
                            '--health-check-port', port,
                            '--health-check-path', '/ready'])
                        query = self.http.last_request.qs
                        self.assertEqual(['0.5'], query['cpu_limit'])
                        self.assertEqual([mode], query['health_check'])
                        self.assertEqual([port], query['health_check_port'])
                        self.assertEqual(['/ready'], query['healthcheck_path'])
                        self.assertNotIn('health_check_path', query)

    def test_invalid_workload_numbers_exit_two(self):
        for cls in (service.ListApp, service.ListService):
            for option, values in (
                    ('--cpu-limit', ('invalid', 'nan', 'inf', '-inf')),
                    ('--health-check-port',
                     ('invalid', '1.5', '0', '-1', '65536'))):
                for value in values:
                    with self.subTest(command=cls, option=option, value=value):
                        cmd = cls(self.app, None)
                        with contextlib.redirect_stderr(io.StringIO()):
                            error = self.assertRaises(
                                SystemExit, cmd.get_parser('test').parse_args,
                                [option + '=' + value])
                        self.assertEqual(2, error.code)

    def test_event_sorting(self):
        self.register('GET', 'events', json={'events': []})
        self.execute(
            catchup.EventList, ['--sort-key', 'action', '--sort-dir', 'asc'])
        self.assertEqual(['action'], self.http.last_request.qs['sort_key'])
        self.assertEqual(['asc'], self.http.last_request.qs['sort_dir'])

    def test_omitted_filters_send_nothing(self):
        for cls, resource, parents, path, envelope in CASES:
            with self.subTest(command=cls):
                self.register('GET', path, json={envelope: []})
                self.execute(cls, parents)
                for field in filters.FIELDS[resource]:
                    self.assertNotIn(field, self.http.last_request.qs)

    def test_event_sort_choices_exit_two(self):
        for option in ('--sort-key', '--sort-dir'):
            cmd = catchup.EventList(self.app, None)
            with contextlib.redirect_stderr(io.StringIO()):
                error = self.assertRaises(
                    SystemExit, cmd.get_parser('test').parse_args,
                    [option, 'invalid'])
            self.assertEqual(2, error.code)

    def test_combined_volume_filters_resolve_server_name(self):
        self.register('GET', fixtures.SERVERS_PATH, json={'servers': [
            {'id': fixtures.SERVER, 'name': 'worker'}]})
        path = fixtures.STACK_PATH + '/volumes'
        self.register('GET', path, json={'volumes': []})
        self.execute(catchup.VolumeList, fixtures.SCOPED + [
            '--name', 'data', '--status', 'IN_USE', '--server', 'worker'])
        query = self.http.last_request.qs
        self.assertEqual(['data'], query['name'])
        self.assertEqual(['in_use'], query['status'])
        self.assertEqual([fixtures.SERVER], query['server_id'])

    def test_named_workload_parents_scope_server_lookup(self):
        other_stack = fixtures.ENVIRONMENT
        self.register('GET', 'stacks', json={'stacks': [
            {'id': fixtures.STACK, 'name': 'shop'},
            {'id': other_stack, 'name': 'other'}]})
        group = {'id': fixtures.APPGROUP, 'name': 'website',
                 'stack_id': fixtures.STACK}
        self.register('GET', fixtures.APPGROUPS_PATH,
                      json={'appgroups': [group]})
        self.register('GET', fixtures.APPGROUP_PATH,
                      json={'appgroup': group})
        self.register('GET', 'stacks/' + other_stack + '/appgroups',
                      json={'appgroups': []})
        self.register('GET', fixtures.ENVIRONMENTS_PATH,
                      json={'environments': [
                          {'id': fixtures.ENVIRONMENT, 'name': 'production',
                           'appgroup_id': fixtures.APPGROUP}]})
        self.register('GET', fixtures.SERVERS_PATH, json={'servers': [
            {'id': fixtures.SERVER, 'name': 'worker'}]})
        other_servers = 'stacks/' + other_stack + '/servers'
        self.register('GET', other_servers, json={'servers': [
            {'id': fixtures.APPGROUP, 'name': 'worker'}]})
        self.register('GET', 'services', json={'services': []})
        parents = (
            ['--stack', 'shop'],
            ['--appgroup', 'website'],
            ['--environment', 'production'],
            ['--appgroup', 'website', '--environment', 'production'],
            ['--stack', 'shop', '--appgroup', 'website',
             '--environment', 'production'],
        )
        for cls in (service.ListApp, service.ListService):
            for options in parents:
                with self.subTest(command=cls, parents=options):
                    before = len(self.http.request_history)
                    self.execute(cls, options + ['--server', 'worker'])
                    query = self.http.last_request.qs
                    self.assertEqual([fixtures.SERVER], query['server_id'])
                    self.assertEqual([cls.kind], query['kind'])
                    if '--stack' in options:
                        self.assertEqual([fixtures.STACK], query['stack_id'])
                    if '--appgroup' in options:
                        self.assertEqual([fixtures.APPGROUP],
                                         query['appgroup_id'])
                    if '--environment' in options:
                        self.assertEqual([fixtures.ENVIRONMENT],
                                         query['environment_id'])
                    history = self.http.request_history[before:]
                    server_requests = [request.path for request in history
                                       if request.path.endswith('/servers')]
                    self.assertEqual(['/v1/' + fixtures.SERVERS_PATH],
                                     server_requests)
                    if options == ['--stack', 'shop']:
                        self.assertEqual(1, sum(
                            request.path == '/v1/stacks'
                            for request in history))
