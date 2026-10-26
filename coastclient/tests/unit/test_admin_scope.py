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

"""Administrative reads preserve scope without client role checks."""
import json

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import deployment
from coastclient.osc.v1 import domain
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.tests.unit import test_commands as fixtures
from coastclient.v1 import client


LISTS = (
    (stack.ListStack, [], 'stacks', 'stacks'),
    (server.ListServer, fixtures.SCOPED, fixtures.SERVERS_PATH, 'servers'),
    (appgroup.ListAppgroup, fixtures.SCOPED,
     fixtures.APPGROUPS_PATH, 'appgroups'),
    (environment.ListEnvironment, ['--appgroup', fixtures.APPGROUP],
     fixtures.ENVIRONMENTS_PATH, 'environments'),
    (service.ListApp, [], 'services', 'services'),
    (service.ListService, [], 'services', 'services'),
    (deployment.ListDeployment, ['--service', fixtures.SERVER],
     'services/' + fixtures.SERVER + '/deployments', 'deployments'),
    (domain.ListDomain, ['--service', fixtures.SERVER],
     'services/' + fixtures.SERVER + '/domains', 'domains'),
    (catchup.StorageList, fixtures.SCOPED,
     fixtures.STACK_PATH + '/storages', 'storages'),
    (catchup.VolumeList, fixtures.SCOPED,
     fixtures.STACK_PATH + '/volumes', 'volumes'),
    (catchup.ExportList, fixtures.SCOPED,
     fixtures.STACK_PATH + '/exports', 'exports'),
    (catchup.EventList, [], 'events', 'events'),
)
READS = (
    (stack.ShowStack, [fixtures.STACK], fixtures.STACK_PATH, 'stack'),
    (catchup.ServerShow, [fixtures.SERVER, *fixtures.SCOPED],
     fixtures.SERVER_PATH, 'server'),
    (catchup.VolumeShow, [fixtures.SERVER, *fixtures.SCOPED],
     fixtures.STACK_PATH + '/volumes/' + fixtures.SERVER, 'volume'),
)


class AdminScopeTest(base.BaseTestCase):
    set_up_api = fixtures.CommandTest.set_up_api
    register = fixtures.CommandTest.register
    execute = fixtures.CommandTest.execute
    assert_no_top_level_parent_lists = (
        fixtures.CommandTest.assert_no_top_level_parent_lists)

    def setUp(self):
        super().setUp()
        self.set_up_api()
        self.http.get(fixtures.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.28',
            'links': [{'rel': 'self', 'href': fixtures.BASE}]}]})
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.28')

    def test_scoped_lists_and_pagination(self):
        for cls, arguments, path, envelope in LISTS:
            for options, query in ((['--all-projects'],
                                    {'all_projects': ['true']}),
                                   (['--project', 'other-project'],
                                    {'project_id': ['other-project']})):
                with self.subTest(command=cls, options=options):
                    row = {'id': fixtures.SERVER, 'project_id': 'owner'}
                    if envelope == 'exports':
                        row = {
                            'id': fixtures.SERVER, 'status': 'PENDING',
                            'volume_ids': [fixtures.SERVER], 'artifacts': [],
                            'object_expires_at': None,
                            'links': [{'rel': 'self', 'href':
                                       fixtures.BASE + path + '/' +
                                       fixtures.SERVER}]}
                        self.register('GET', fixtures.STACK_PATH,
                                      json={'stack': {'project_id': 'owner'}})
                    self.register('GET', path, json={
                        envelope: [row], 'links': [{
                            'rel': 'next',
                            'href': (fixtures.BASE + path + '?marker=' +
                                     fixtures.SERVER)}]})
                    # Match the second page after the generic first page.
                    self.register('GET', path + '?marker=' + fixtures.SERVER,
                                  json={envelope: [row]})
                    before = len(self.http.request_history)
                    columns, values = self.execute(cls, arguments + options)
                    values = list(values)
                    self.assertEqual(['owner', 'owner'],
                                     [r[columns.index('Project ID')]
                                      for r in values])
                    requests = self.http.request_history[before:]
                    pages = [r for r in requests
                             if r.path == '/v1/' + path]
                    self.assertEqual(2, len(pages))
                    for request in pages:
                        for key, value in query.items():
                            self.assertEqual(value, request.qs[key])
                    self.assertEqual([fixtures.SERVER],
                                     pages[-1].qs['marker'])
                    if envelope == 'exports':
                        parents = [r for r in requests
                                   if r.path == '/v1/' + fixtures.STACK_PATH]
                        self.assertEqual(1, len(parents))
                        self.assertEqual({}, parents[0].qs)
                    if envelope in ('servers', 'volumes'):
                        self.assertTrue(all(
                            r.qs['all_projects'] == ['true'] for r in pages))

    def test_default_lists_omit_project_column(self):
        for cls, arguments, path, envelope in LISTS:
            with self.subTest(command=cls):
                self.register('GET', path, json={envelope: []})
                columns, values = self.execute(cls, arguments)
                self.assertNotIn('Project ID', columns)
                self.assertEqual([], list(values))
                self.assertNotIn('all_projects', self.http.last_request.qs)
                self.assertNotIn('project_id', self.http.last_request.qs)

    def test_empty_project_fails_before_request(self):
        for cls, arguments, _path, _envelope in LISTS:
            for project in ('', '   '):
                with self.subTest(command=cls, project=project):
                    before = len(self.http.request_history)
                    error = self.assertRaises(
                        SystemExit, self.execute, cls,
                        arguments + ['--project', project])
                    self.assertEqual(2, error.code)
                    self.assertEqual(before, len(self.http.request_history))

    def test_mutual_exclusion_and_help(self):
        for cls, arguments, _path, _envelope in LISTS:
            parser = cls(self.app, None).get_parser('test')
            self.assertIn('(admin only)', parser.format_help())
            self.assertRaises(SystemExit, parser.parse_args,
                              arguments + ['--all-projects', '--project', 'p'])

    def test_scoped_references_require_uuids(self):
        cases = [(server.ListServer, ['--stack', 'demo']),
                 (environment.ListEnvironment, ['--appgroup', 'demo']),
                 (service.ListApp, ['--environment', 'demo']),
                 (deployment.ListDeployment, ['--service', 'demo']),
                 (catchup.StorageList, ['--stack', fixtures.STACK,
                                        '--service', 'demo']),
                 (stack.ShowStack, ['demo']),
                 (stack.ListStackEvent, ['demo'])]
        for cls, arguments in cases:
            for options in (['--all-projects'], ['--project', 'p']):
                if options[0] == '--project' and cls in (
                        stack.ShowStack, stack.ListStackEvent):
                    continue
                with self.subTest(command=cls, options=options):
                    self.assertRaisesRegex(
                        exceptions.CommandError, 'UUID is required',
                        self.execute, cls, arguments + options)

    def test_show_scope_and_version_gate(self):
        for cls, arguments, path, envelope in READS:
            with self.subTest(command=cls):
                self.register('GET', path, json={envelope: {
                    'id': fixtures.SERVER, 'project_id': 'owner'}})
                self.execute(cls, arguments + ['--all-projects'])
                self.assertEqual(['true'],
                                 self.http.last_request.qs['all_projects'])
                self.app.client_manager.coast = client.Client(
                    self.session, region_name='region', microversion='1.17')
                self.assertRaisesRegex(
                    exceptions.CommandError, 'requires Coast API 1.18',
                    self.execute, cls, arguments + ['--all-projects'])
                self.app.client_manager.coast = client.Client(
                    self.session, region_name='region', microversion='1.28')

    def test_stack_timeline(self):
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.18')
        path = fixtures.STACK_PATH + '/events'
        self.register('GET', fixtures.STACK_PATH,
                      json={'stack': {'project_id': 'owner'}})
        self.register('GET', path, json={'events': [{'id': fixtures.SERVER}],
                      'links': [{'rel': 'next', 'href': fixtures.BASE + path +
                                 '?marker=' + fixtures.SERVER}]})
        self.register('GET', path + '?marker=' + fixtures.SERVER,
                      json={'events': [{'id': fixtures.SERVER}]})
        columns, values = self.execute(
            stack.ListStackEvent, [fixtures.STACK, '--all-projects'])
        self.assertEqual(['owner', 'owner'],
                         [r[columns.index('Project ID')] for r in values])
        pages = [r for r in self.http.request_history
                 if r.path == '/v1/' + path]
        self.assertEqual(2, len(pages))
        self.assertTrue(all(r.qs['all_projects'] == ['true'] for r in pages))
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.17')
        self.assertRaisesRegex(
            exceptions.CommandError, 'requires Coast API 1.18', self.execute,
            stack.ListStackEvent, [fixtures.STACK, '--all-projects'])

    def test_api_forbidden_is_preserved(self):
        for cls, arguments, path, _envelope in LISTS + READS + (
                (stack.ListStackEvent, [fixtures.STACK],
                 fixtures.STACK_PATH + '/events', 'events'),):
            with self.subTest(command=cls):
                self.register('GET', path, status_code=403, json={
                    'faultcode': 'Client', 'faultstring': 'Access denied.'})
                self.assertRaisesRegex(
                    exceptions.CommandError, r'Access denied.*HTTP 403',
                    self.execute, cls, arguments + ['--all-projects'])

    def test_unrelated_show_and_lists_have_no_scope_options(self):
        for cls in (appgroup.ShowAppgroup, environment.ShowEnvironment,
                    service.ShowApp, domain.ShowDomain,
                    catchup.StorageShow, catchup.ExportShow,
                    catchup.BackupList, catchup.TemplateList,
                    catchup.PlanList, catchup.FileMountList):
            parser = cls(self.app, None).get_parser('test')
            self.assertNotIn('--all-projects', parser.format_help())
            self.assertNotIn('--project ', parser.format_help())

    def test_scoped_long_machine_output(self):
        self.register('GET', 'stacks', json={'stacks': [{
            'id': fixtures.STACK, 'project_id': 'owner', 'name': 'demo'}]})
        self.execute(
            stack.ListStack, ['--all-projects', '--long', '-f', 'json'],
            formatted=True)
        result = json.loads(self.app.stdout.getvalue())
        self.assertEqual('owner', result[0]['Project ID'])
        self.assertIn('Description', result[0])

    def test_scoped_empty_output_has_project_column(self):
        self.register('GET', 'stacks', json={'stacks': []})
        for options in (['--all-projects'], ['--project', 'project-id']):
            columns, values = self.execute(stack.ListStack, options)
            self.assertIn('Project ID', columns)
            self.assertEqual([], list(values))
