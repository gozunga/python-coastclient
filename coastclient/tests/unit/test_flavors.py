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

"""M5 flavor catalog, server output and workload kind contracts."""
import io
import json
from types import SimpleNamespace

from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from requests_mock.contrib import fixture

from osc_lib import exceptions
from oslotest import base
import yaml

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import flavor
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase4
from coastclient.tests.unit import test_phase6
from coastclient.v1 import client


class FlavorTest(base.BaseTestCase):
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

    def select(self, maximum, version=None):
        self.http.get(test_commands.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': maximum, 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion=version)
        self.app.client_manager.coast = self.api

    def test_flavor_columns_and_per_call_version(self):
        row = {'id': 'flavor-id', 'name': 'Small', 'plan': 'small',
               'vcpus': 1, 'ram': 1024, 'disk': 10,
               'roles': ['worker', 'build']}
        for selected in ('1.0', '1.28'):
            self.select('1.28', selected)
            self.register('GET', 'flavors', json={'flavors': [row]},
                          complete_qs=True)
            columns, rows = self.execute(flavor.ListFlavor, [])
            self.assertEqual(('ID', 'Name', 'Plan', 'vCPUs', 'RAM (MiB)',
                              'Disk (GiB)', 'Roles'), columns)
            values = list(rows)[0]
            self.assertEqual(('flavor-id', 'Small', 'small', 1, 1024, 10),
                             values[:6])
            self.assertEqual(['build', 'worker'],
                             values[-1].machine_readable())
            self.assertEqual('build, worker', values[-1].human_readable())
            version = '1.27' if selected == '1.0' else '1.28'
            self.assert_request('GET', 'flavors', version=version)
            self.assertFalse(self.http.last_request.qs)
            self.assertEqual(selected, self.api.microversion)
            self.register('GET', 'services', json={'services': []})
            self.api.request('/services', 'GET')
            self.assert_request('GET', 'services', version=selected)

    def test_plan_and_flavor_roles_in_all_formatters(self):
        self.select('1.28')
        for roles in (['worker', 'build'], ['worker', 'control', 'build'], []):
            plan = {'name': 'small', 'kind': 'vm', 'roles': roles,
                    'description': '3 GB RAM', 'flavor': 'hidden'}
            self.register('GET', 'plans', json={'plans': [plan]})
            self.register('GET', 'flavors', json={'flavors': [
                dict(plan, id='flavor-id', plan='small', ram=3072,
                     disk=10, vcpus=1)]})
            for cls in (catchup.PlanList, flavor.ListFlavor):
                for formatter in ('table', 'json', 'yaml'):
                    with self.subTest(roles=roles, command=cls.__name__,
                                      formatter=formatter):
                        self.app.stdout = io.StringIO()
                        self.execute(cls, ['-f', formatter], True)
                        output = self.app.stdout.getvalue()
                        if formatter == 'table':
                            self.assertNotIn("['", output)
                            if roles:
                                self.assertIn(', '.join(sorted(roles)), output)
                        else:
                            rows = (json.loads(output) if formatter == 'json'
                                    else yaml.safe_load(output))
                            self.assertEqual(sorted(roles), rows[0]['Roles'])
                            if cls == catchup.PlanList:
                                self.assertEqual(
                                    {'Name', 'Kind', 'Roles', 'Description'},
                                    set(rows[0]))
                                self.assertEqual('3 GB RAM',
                                                 rows[0]['Description'])

    def test_old_api_and_version_errors(self):
        for maximum, status in (('1.26', None), ('1.28', 404), ('1.28', 406)):
            self.select(maximum, '1.0')
            route = self.register('GET', 'flavors', status_code=status or 200,
                                  json={'flavors': []})
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      flavor.ListFlavor, [])
            self.assertIn('needs Coast API 1.27 or later', str(error))
            self.assertEqual(0 if status is None else 1, route.call_count)

    def test_empty_catalog_and_no_query_options(self):
        self.select('1.28')
        self.register('GET', 'flavors', json={'flavors': []})
        _, rows = self.execute(flavor.ListFlavor, [])
        self.assertEqual([], list(rows))
        for option in ('--limit', '--marker', '--sort-key'):
            self.assertRaises(SystemExit, self.execute, flavor.ListFlavor,
                              [option, '1'])

    def test_flavor_hint_is_route_and_method_scoped(self):
        for path, method, version in (('/services', 'GET', '1.27'),
                                      ('/flavors', 'POST', '1.27'),
                                      ('/flavors', 'GET', '1.26')):
            self.assertRaises(ValueError, self.api.request, path, method,
                              flavors_microversion=version)

    def test_server_flavor_output(self):
        full = {'id': 'flavor-id', 'name': 'Small', 'vcpus': 1,
                'ram': 1024, 'disk': 10}
        for value, expected in ((full, 'Small'),
                                ({'id': 'gone', 'name': None}, 'gone'),
                                (None, '')):
            row = {'id': test_commands.SERVER, 'flavor': value}
            self.register('GET', test_commands.SERVERS_PATH,
                          json={'servers': [row]})
            columns, rows = self.execute(server.ListServer,
                                         test_commands.SCOPED)
            listed = list(rows)[0][columns.index('Flavor')]
            self.assertEqual(value, listed.machine_readable())
            self.assertIn(expected, listed.human_readable())
            self.register('GET', test_commands.SERVER_PATH,
                          json={'server': row})
            columns, values = self.execute(catchup.ServerShow,
                                           [test_commands.SERVER,
                                            *test_commands.SCOPED])
            result = values[columns.index('Flavor')]
            self.assertEqual(listed.machine_readable(),
                             result.machine_readable())
            self.assertEqual(listed.human_readable(),
                             result.human_readable())
            self.assertIn(expected, result.human_readable())
            if value == full:
                self.assertEqual('Small, 1 vCPUs, 1024 MiB RAM, 10 GiB disk',
                                 result.human_readable())
        self.register('GET', test_commands.SERVERS_PATH,
                      json={'servers': [{'id': test_commands.SERVER}]})
        columns, rows = self.execute(server.ListServer, test_commands.SCOPED)
        self.assertEqual('', list(rows)[0][columns.index('Flavor')]
                         .human_readable())

    def test_server_show_formatters(self):
        value = {'id': 'flavor-id', 'name': 'Small', 'vcpus': 1,
                 'ram': 1024, 'disk': 10}
        row = {'id': test_commands.SERVER, 'flavor': value}
        self.register('GET', test_commands.SERVER_PATH, json={'server': row})
        self.register('GET', test_commands.SERVERS_PATH,
                      json={'servers': [row]})
        for cls, argv in ((catchup.ServerShow,
                           [test_commands.SERVER, *test_commands.SCOPED]),
                          (server.ListServer, test_commands.SCOPED)):
            for formatter in ('json', 'yaml', 'table'):
                with self.subTest(command=cls.__name__, formatter=formatter):
                    self.app.stdout = io.StringIO()
                    self.execute(cls, [*argv, '-f', formatter], True)
                    output = self.app.stdout.getvalue()
                    if formatter == 'table':
                        self.assertIn(
                            'Small, 1 vCPUs, 1024 MiB RAM, 10 GiB disk',
                            output)
                    else:
                        data = (json.loads(output) if formatter == 'json'
                                else yaml.safe_load(output))
                        if cls is server.ListServer:
                            data = data[0]
                        self.assertEqual(value, data['Flavor'])

    def test_installed_flavor_command(self):
        self.select('1.28')
        self.register('GET', 'flavors', json={'flavors': []})
        status, app = test_phase6.Phase6Test.run_shell(
            self, ['coast', 'flavor', 'list', '-f', 'json'])
        self.assertEqual(0, status)
        self.assertEqual([], json.loads(app.stdout.getvalue()))

    def test_server_plan_and_resize_flavor_help(self):
        for cls, option in ((server.AddServer, '--plan'),
                            (server.ResizeServer, '--flavor')):
            with self.subTest(command=cls.__name__):
                parser = cls(self.app, None).get_parser('test')
                action = next(action for action in parser._actions
                              if option in action.option_strings)
                guidance = ('openstack coast plan list'
                            if cls is server.AddServer else
                            'openstack coast flavor list')
                self.assertIn(guidance, action.help)
                self.assertIn(guidance, parser.format_help())

    def test_explicit_image_kinds_at_128(self):
        self.select('1.28')
        self.assertEqual('1.28', self.api.microversion)
        self.register('POST', 'services', status_code=202,
                      json={'service': {'id': test_phase4.SERVICE}})
        for cls, kind in ((service.CreateApp, 'app'),
                          (service.CreateService, 'service')):
            self.app.stdout = io.StringIO()
            self.execute(cls, [*test_phase4.CREATE[:7], '--image',
                               'registry.example.com/web', '--tag', 'release'])
            request = self.http.last_request
            self.assertEqual(kind, request.json()['service']['kind'])
            self.assertEqual('app-hosting 1.28',
                             request.headers['OpenStack-API-Version'])
