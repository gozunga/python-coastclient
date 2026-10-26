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

"""M5 database commands against a mocked microversion 1.29 API."""
import gzip
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock
from urllib import parse

from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from osc_lib import exceptions
from oslotest import base
from requests_mock.contrib import fixture

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import database
from coastclient.tests.unit import test_commands as common
from coastclient.v1 import client


# Engine names and versions are API fixtures, never client choices.
ENGINE = {'engine': 'fixture-engine', 'display_name': 'Fixture Engine',
          'versions': ['current', 'previous'], 'default_version': 'current',
          'port': 1234, 'backups': True, 'has_username': True,
          'has_database': True}
IDENTITY = '11111111-1111-1111-1111-111111111111'
PATH = 'databases/' + IDENTITY
ROW = {'id': IDENTITY, 'name': 'orders', 'engine': ENGINE['engine'],
       'version': ENGINE['default_version'], 'status': 'ACTIVE'}


class DatabaseTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.http = self.useFixture(fixture.Fixture())
        self.register('GET', 'services', json={'services': []})
        self.addCleanup(self.assert_no_top_level_parent_lists)
        token = ks_fixture.V3Token(project_id='project')
        token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region', url=common.ROOT)
        auth_url = 'https://identity.example.com/v3'
        self.http.post(auth_url + '/auth/tokens', json=token,
                       headers={'X-Subject-Token': 'test-token'})
        self.session = session.Session(auth=v3.Token(
            auth_url=auth_url, token='input-token', project_id='project'))
        self.http.get(common.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.1', 'links': [
                {'rel': 'self', 'href': common.BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.0')
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO())
        self.select('1.29', '1.0')
        self.app.stderr = io.StringIO()
        self.register('GET', 'database_engines',
                      json={'database_engines': [ENGINE]})
        self.register('GET', 'databases', json={'databases': [ROW]})
        self.register('GET', PATH, json={'database': ROW})

    execute = common.CommandTest.execute
    register = common.CommandTest.register
    assert_request = common.CommandTest.assert_request
    assert_no_top_level_parent_lists = (
        common.CommandTest.assert_no_top_level_parent_lists)

    def select(self, maximum, version=None):
        self.http.get(common.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': maximum, 'links': [
                {'rel': 'self', 'href': common.BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion=version)
        self.app.client_manager.coast = self.api

    def test_catalog_columns_and_version(self):
        columns, rows = self.execute(database.ListDatabaseEngine, [])
        self.assertEqual(('Engine', 'Name', 'Versions', 'Default', 'Port',
                          'Backups', 'Has database', 'Has username'), columns)
        self.assertEqual(ENGINE['engine'], list(rows)[0][0])
        self.assert_request('GET', 'database_engines', version='1.29')
        self.assertEqual('1.0', self.api.microversion)

    def test_database_updates_and_backups_at_negotiated_131(self):
        self.select('1.31')
        self.assertEqual('1.31', self.api.microversion)
        self.execute(database.ListDatabaseEngine, [])
        self.assert_request('GET', 'database_engines', version='1.31')
        self.register('PATCH', PATH, json={'database': ROW})
        self.execute(database.SetDatabase, [IDENTITY, '--cpu-limit', '0.5'])
        self.assert_request('PATCH', PATH, {'database': {'cpu_limit': 0.5}},
                            version='1.31')
        self.register('PUT', PATH + '/backup_policy',
                      json={'backup_policy': {'enabled': True}})
        self.execute(database.SetDatabaseBackupPolicy, [IDENTITY, '--enable'])
        self.assert_request('PUT', PATH + '/backup_policy',
                            {'backup_policy': {'enabled': True}},
                            version='1.31')
        self.register('POST', PATH + '/backups',
                      json={'backup': {'id': 'backup', 'status': 'PENDING'}})
        self.execute(database.CreateDatabaseBackup, [IDENTITY])
        self.assert_request('POST', PATH + '/backups', {}, version='1.31')
        self.assertEqual('1.31', self.api.microversion)

    def test_catalog_capabilities(self):
        engines = [dict(ENGINE, engine='fixture-%s-%s' % (has_db, has_user),
                        has_database=has_db, has_username=has_user)
                   for has_db in (True, False)
                   for has_user in (True, False)]
        self.register('GET', 'database_engines',
                      json={'database_engines': engines})
        columns, rows = self.execute(database.ListDatabaseEngine, [])
        expected = [(row['has_database'], row['has_username'])
                    for row in engines]
        results = [dict(zip(columns, row)) for row in rows]
        self.assertEqual(expected, [(row['Has database'], row['Has username'])
                                    for row in results])
        self.execute(database.ListDatabaseEngine, ['-f', 'json'],
                     formatted=True)
        results = json.loads(self.app.stdout.getvalue())
        self.assertEqual(expected, [(row['Has database'], row['Has username'])
                                    for row in results])

    def create_args(self):
        return ['orders', '--engine', ENGINE['engine'],
                '--appgroup', common.APPGROUP,
                '--environment', common.ENVIRONMENT,
                '--server', common.SERVER]

    def test_create_and_list_help(self):
        for cls in (database.CreateDatabase, database.ListDatabase):
            parser = cls(self.app, None).get_parser('coast database')
            help_text = ' '.join(parser.format_help().split())
            self.assertNotIn('value.', help_text)
            if cls is database.CreateDatabase:
                for option in ('--database', '--username'):
                    option_help = parser._option_string_actions[option].help
                    self.assertIn('engine list', option_help)
                    self.assertIn('default: app', option_help)
                self.assertIn('Coast generates a strong password', help_text)
                self.assertIn('coast database credentials show', help_text)
                self.assertIn('Free-text description', help_text)
            else:
                for label in ('stack', 'app group', 'environment', 'server'):
                    self.assertIn('Only databases in this %s (name or ID).'
                                  % label, help_text)
                self.assertIn('Only databases of this engine', help_text)
                self.assertIn('engine list', help_text)
                self.assertIn('for example ACTIVE or ERROR', help_text)

    def test_create_exact_help(self):
        parser = database.CreateDatabase(self.app, None).get_parser('test')
        expected = {
            'name': 'Name shown in Coast; you type it to confirm a delete '
                    'in the dashboard.',
            'database': 'Initial database created inside the engine '
                        '(default: app). Only for engines that have '
                        'databases; '
                        'see coast database engine list.',
            'username': 'Database user for your app (default: app). Only for '
                        'engines that support named users; '
                        'see coast database engine list.',
        }
        for action in parser._actions:
            if action.dest in expected:
                self.assertEqual(expected.pop(action.dest), action.help)
        self.assertEqual({}, expected)
        self.assertEqual(
            'Create a database using an engine and version from the catalog. '
            'Coast generates a strong password and stores it securely; reveal '
            'it with coast database credentials show NAME --show-password.',
            ' '.join(parser.description.split()))

    def test_create_notice_preserves_output(self):
        row = dict(ROW, name='created-name', database='app', username='app')
        self.register('POST', 'databases', status_code=202,
                      json={'database': row})
        self.register('GET', PATH, json={'database': row})
        notice = ('Coast generated a strong password. Reveal it with: '
                  'coast database credentials show created-name '
                  '--show-password\n')
        for wait in ([], ['--wait']):
            columns, values = self.execute(
                database.CreateDatabase, self.create_args() + wait)
            self.assertEqual(('Allowed from', 'Database', 'Engine', 'ID',
                              'Name', 'Public', 'Public URL', 'Status',
                              'Username', 'Version'), columns)
            self.assertEqual('app', dict(zip(columns, values))['Database'])
            self.assertEqual(notice, self.app.stderr.getvalue())
            self.app.stderr = io.StringIO()
        for fmt in ('table', 'json', 'yaml', 'shell', 'value'):
            for selected in ([], ['-c', 'Database']):
                flags = ['-f', fmt] + selected
                self.app.stdout = io.StringIO()
                self.execute(database.ShowDatabase, [IDENTITY] + flags,
                             formatted=True)
                expected = self.app.stdout.getvalue()
                self.app.stdout = io.StringIO()
                self.execute(database.CreateDatabase,
                             self.create_args() + flags, formatted=True)
                self.assertEqual(expected, self.app.stdout.getvalue())
                self.assertNotIn('password', self.app.stdout.getvalue())
                self.assertEqual(notice, self.app.stderr.getvalue())
                self.app.stderr = io.StringIO()
        self.assertFalse(any('/credentials' in r.url
                             for r in self.http.request_history))

    def test_failed_create_has_no_password_notice(self):
        self.register('POST', 'databases', status_code=400,
                      json={'error': 'Invalid request'})
        self.assertRaises(exceptions.CommandError, self.execute,
                          database.CreateDatabase, self.create_args())
        self.assertEqual('', self.app.stderr.getvalue())

    def test_create_body_and_wait(self):
        self.register('POST', 'databases', status_code=202,
                      json={'database': dict(ROW, status='PENDING')})
        self.execute(database.CreateDatabase, self.create_args())
        self.assert_request('POST', 'databases', {'database': {
            'name': 'orders', 'engine': ENGINE['engine'],
            'appgroup_id': common.APPGROUP,
            'environment_id': common.ENVIRONMENT, 'server_id': common.SERVER}},
            version='1.29')
        columns, values = self.execute(database.CreateDatabase,
                                       self.create_args() + ['--wait'])
        self.assertEqual('ACTIVE', dict(zip(columns, values))['Status'])

    def test_catalog_validates_choices_and_capabilities(self):
        for extra in (['--engine', 'missing'], ['--version', 'missing']):
            self.assertRaises(exceptions.CommandError, self.execute,
                              database.CreateDatabase,
                              self.create_args() + extra)
        self.register('GET', 'database_engines', json={'database_engines': [
            dict(ENGINE, has_username=False, has_database=False)]})
        for option in ('--username', '--database'):
            self.assertRaises(exceptions.CommandError, self.execute,
                              database.CreateDatabase,
                              self.create_args() + [option, 'app'])
        self.assertFalse(any(r.method == 'POST'
                             for r in self.http.request_history
                             if '/databases' in r.url))

    def test_show_by_name_and_named_missing(self):
        columns, values = self.execute(database.ShowDatabase, ['orders'])
        self.assertEqual(IDENTITY, dict(zip(columns, values))['ID'])
        self.assert_request('GET', PATH, version='1.29')
        self.register('GET', PATH, status_code=404)
        error = self.assertRaises(exceptions.CommandError, self.execute,
                                  database.ShowDatabase, ['orders'])
        self.assertEqual("Database 'orders' not found.", str(error))
        self.assertRaises(exceptions.CommandError, self.execute,
                          database.ShowDatabase, ['missing'])
        self.register('GET', 'databases', json={'databases': [ROW, ROW]})
        error = self.assertRaises(exceptions.CommandError, self.execute,
                                  database.ShowDatabase, ['orders'])
        self.assertIn('More than one database', str(error))

    def test_list_filters_and_pagination(self):
        self.register('GET', 'databases', json={'databases': [ROW], 'links': [
            {'rel': 'next', 'href': common.BASE + 'databases?marker=' +
             IDENTITY}]})
        self.register('GET', 'databases?marker=' + IDENTITY,
                      complete_qs=True, json={'databases': [ROW]})
        columns, rows = self.execute(database.ListDatabase, [
            '--stack', common.STACK, '--appgroup', common.APPGROUP,
            '--environment', common.ENVIRONMENT, '--server', common.SERVER,
            '--engine', ENGINE['engine'], '--status', 'ACTIVE'])
        self.assertEqual(2, len(list(rows)))
        req = next(r for r in self.http.request_history
                   if r.path == '/v1/databases' and 'engine' in r.qs)
        self.assertEqual([common.STACK], req.qs['stack_id'])
        self.assertEqual([common.APPGROUP], req.qs['appgroup_id'])
        self.assertEqual([common.ENVIRONMENT], req.qs['environment_id'])
        self.assertEqual([common.SERVER], req.qs['server_id'])
        self.assertEqual(['active'], req.qs['status'])

    def test_actions_and_wait(self):
        for cls, action, target in (
                (database.StartDatabase, 'start', 'ACTIVE'),
                (database.StopDatabase, 'stop', 'STOPPED'),
                (database.RestartDatabase, 'restart', 'ACTIVE')):
            self.register('POST', PATH + '/action', status_code=202)
            self.execute(cls, ['orders'])
            self.assert_request('POST', PATH + '/action', {action: {}},
                                version='1.29')
            intermediate = 'STOPPING' if action == 'stop' else 'STARTING'
            poll = self.http.get(common.BASE + PATH, [
                {'json': {'database': dict(ROW, status=intermediate)}},
                {'json': {'database': dict(ROW, status=target)}},
                {'json': {'database': dict(ROW, status='ERROR')}}])
            count = len(self.http.request_history)
            with mock.patch.object(database, 'wait_for',
                                   wraps=database.wait_for) as wait:
                with mock.patch.object(database.time, 'sleep') as sleep:
                    self.execute(cls, [IDENTITY, '--wait'])
            wait.assert_called_once_with(self.api, IDENTITY, target)
            self.assertEqual(2, poll.call_count)
            sleep.assert_called_once_with(2)
            requests = self.http.request_history[count:]
            self.assertEqual(
                [('POST', '/v1/' + PATH + '/action'),
                 ('GET', '/v1/' + PATH), ('GET', '/v1/' + PATH)],
                [(request.method, request.path) for request in requests])
            for request in requests:
                self.assertEqual('app-hosting 1.29',
                                 request.headers['OpenStack-API-Version'])

    def test_delete_multiple_and_wait(self):
        self.register('DELETE', PATH, status_code=202)
        self.execute(database.DeleteDatabase, ['orders', IDENTITY])
        self.assertEqual(2, sum(r.method == 'DELETE'
                                for r in self.http.request_history))
        self.register('GET', PATH, status_code=404)
        self.execute(database.DeleteDatabase, [IDENTITY, '--wait'])
        self.assert_request('GET', PATH, version='1.29')

    def test_wait_pending_error_and_timeout(self):
        self.http.get(common.BASE + PATH, [
            {'json': {'database': dict(ROW, status='STARTING')}},
            {'json': {'database': ROW}}])
        with mock.patch.object(database.time, 'sleep') as sleep:
            self.assertEqual(ROW, database.wait_for(self.api, IDENTITY,
                                                    'ACTIVE'))
            sleep.assert_called_once_with(2)
        self.register('GET', PATH,
                      json={'database': dict(ROW, status='ERROR')})
        self.assertRaisesRegex(exceptions.CommandError, 'entered ERROR',
                               database.wait_for, self.api, IDENTITY, 'ACTIVE')
        self.register('GET', PATH,
                      json={'database': dict(ROW, status='PENDING')})
        with mock.patch.object(database.time, 'monotonic',
                               side_effect=[0, 1201]):
            self.assertRaisesRegex(exceptions.CommandError, 'Timed out',
                                   database.wait_for, self.api, IDENTITY,
                                   'ACTIVE')

    def test_logs_filters_and_notices(self):
        self.register('GET', PATH + '/logs', json={'logs': {
            'unavailable': 'Temporarily unavailable', 'lines': [
                {'ts': 'now', 'stream': 'stdout', 'line': 'ready'}]}})
        columns, rows = self.execute(database.LogsDatabase, [
            'orders', '--tail', '0', '--since', '2026-10-23T00:00:00Z'])
        self.assertEqual([('now', 'stdout', 'ready')], list(rows))
        self.assertEqual(['0'], self.http.last_request.qs['tail'])
        query = parse.parse_qs(
            parse.urlsplit(self.http.last_request.url).query)
        self.assertEqual(['2026-10-23T00:00:00Z'], query['since'])
        self.assertIn('Temporarily unavailable', self.app.stderr.getvalue())

    def test_credentials_mask_all_password_bearing_fields(self):
        secret = 'fixture-password'
        credentials = {'username': 'app', 'database': 'app',
                       'password': secret,
                       'root_password': secret,
                       'internal_url': 'db://app:' + secret + '@db/app',
                       'public_url': 'db://app:' + secret + '@public/app'}
        self.register('GET', PATH + '/credentials',
                      json={'credentials': credentials})
        for flags in ([], ['--show-password']):
            columns, values = self.execute(database.ShowDatabaseCredentials,
                                           ['orders'] + flags)
            result = dict(zip(columns, values))
            if flags:
                self.assertEqual(credentials, result)
            else:
                self.assertNotIn(secret, repr(result))
                self.assertEqual('********', result['password'])
        self.execute(database.ShowDatabaseCredentials, [
            IDENTITY, '--show-password', '-f', 'value', '-c', 'password'],
            formatted=True)
        self.assertEqual(secret + '\n', self.app.stdout.getvalue())

    def test_older_server_gets_no_database_requests(self):
        self.select('1.28', '1.0')
        count = len(self.http.request_history)
        self.assertRaises(exceptions.CommandError, self.execute,
                          database.ListDatabaseEngine, [])
        self.assertEqual(count, len(self.http.request_history))

    def test_default_negotiates_129(self):
        self.select('1.29')
        self.assertEqual('1.29', self.api.microversion)

    def test_create_optional_fields_and_parent_names(self):
        self.register('GET', 'stacks', json={'stacks': [
            {'id': common.STACK, 'name': 'shop'}]})
        self.register('GET', 'stacks/' + common.STACK + '/appgroups',
                      json={'appgroups': [{'id': common.APPGROUP,
                                           'name': 'shop'}]})
        self.register('GET', 'appgroups/' + common.APPGROUP,
                      json={'appgroup': {'id': common.APPGROUP,
                                         'stack_id': common.STACK}})
        self.register('GET', 'appgroups/' + common.APPGROUP + '/environments',
                      json={'environments': [{'id': common.ENVIRONMENT,
                                              'name': 'production'}]})
        self.register('GET', 'stacks/' + common.STACK + '/servers',
                      json={'servers': [{'id': common.SERVER,
                                         'name': 'worker'}]})
        self.register('POST', 'databases', status_code=202,
                      json={'database': ROW})
        self.execute(database.CreateDatabase, [
            'orders', '--engine', ENGINE['engine'],
            '--version', ENGINE['versions'][1], '--database', 'app',
            '--username', 'owner', '--description', 'Order data',
            '--cpu-limit', '0.5', '--memory-limit-mb', '256',
            '--appgroup', 'shop', '--environment', 'production',
            '--server', 'worker'])
        self.assert_request('POST', 'databases', {'database': {
            'name': 'orders', 'engine': ENGINE['engine'],
            'version': ENGINE['versions'][1], 'database': 'app',
            'username': 'owner', 'description': 'Order data',
            'cpu_limit': 0.5, 'memory_limit_mb': 256,
            'appgroup_id': common.APPGROUP,
            'environment_id': common.ENVIRONMENT, 'server_id': common.SERVER}},
            version='1.29')

    def test_admin_scope_and_project_column(self):
        cases = ((['--all-projects'], {'all_projects': ['true']}),
                 (['--project', 'project'], {'project_id': ['project']}))
        for flags, expected in cases:
            columns, rows = self.execute(database.ListDatabase, flags)
            self.assertIn('Project ID', columns)
            self.assertEqual(expected, self.http.last_request.qs)
            list(rows)
        self.assertRaisesRegex(
            exceptions.CommandError, 'explicit resource UUID', self.execute,
            database.ListDatabase, ['--all-projects', '--stack', 'shop'])

    def test_named_404s_and_action_faults(self):
        for cls, method, suffix in (
                (database.LogsDatabase, 'GET', '/logs'),
                (database.ShowDatabaseCredentials, 'GET', '/credentials'),
                (database.StartDatabase, 'POST', '/action'),
                (database.DeleteDatabase, 'DELETE', '')):
            self.register(method, PATH + suffix, status_code=404)
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      cls, ['orders'])
            self.assertEqual("Database 'orders' not found.", str(error))
        self.register('GET', PATH + '/credentials', status_code=403)
        self.assertRaisesRegex(exceptions.CommandError, 'HTTP 403',
                               self.execute, database.ShowDatabaseCredentials,
                               [IDENTITY])
        self.register('POST', PATH + '/action', status_code=409, json={
            'faultcode': 'Client', 'faultstring': 'Invalid state',
            'current_status': 'STOPPED'})
        self.assertRaisesRegex(exceptions.CommandError, 'STOPPED',
                               self.execute, database.StopDatabase, [IDENTITY])

    def test_set_partial_updates_and_unset_limits(self):
        self.register('PATCH', PATH, json={'database': ROW})
        cases = [
            (database.SetDatabase, ['--name', 'renamed', '--description', '',
                                    '--cpu-limit', '0.5',
                                    '--memory-limit-mb', '256'],
             {'name': 'renamed', 'description': '', 'cpu_limit': 0.5,
              'memory_limit_mb': 256}),
            (database.SetDatabase, ['--public', '--public-port', '15433',
                                    '--allow-from', '198.51.100.0/24'],
             {'public': {'enabled': True, 'port': 15433,
                         'source': '198.51.100.0/24'}}),
            (database.SetDatabase, ['--no-public'],
             {'public': {'enabled': False}}),
            (database.SetDatabase, ['--allow-from', '192.0.2.0/24'],
             {'public': {'source': '192.0.2.0/24'}}),
            (database.UnsetDatabase, ['--cpu-limit'], {'cpu_limit': None}),
            (database.UnsetDatabase, ['--memory-limit-mb'],
             {'memory_limit_mb': None}),
            (database.UnsetDatabase, ['--cpu-limit', '--memory-limit-mb'],
             {'cpu_limit': None, 'memory_limit_mb': None})]
        for cls, flags, values in cases:
            self.execute(cls, ['orders'] + flags)
            self.assert_request('PATCH', PATH, {'database': values},
                                version='1.29')
        for cls in (database.SetDatabase, database.UnsetDatabase):
            self.assertRaisesRegex(
                exceptions.CommandError, 'Nothing to change', self.execute,
                cls, [IDENTITY])

    def test_create_public_fields_and_no_client_defaults(self):
        self.register('POST', 'databases', json={'database': ROW})
        self.execute(database.CreateDatabase, self.create_args() + [
            '--public', '--public-port', '15433',
            '--allow-from', '198.51.100.0/24'])
        values = self.http.last_request.json()['database']
        self.assertEqual({'enabled': True, 'port': 15433,
                          'source': '198.51.100.0/24'}, values['public'])
        self.execute(database.CreateDatabase,
                     self.create_args() + ['--public'])
        self.assertEqual({'enabled': True},
                         self.http.last_request.json()['database']['public'])

    def test_show_public_columns_and_restart_notice(self):
        self.register('GET', PATH, json={'database': dict(
            ROW, public={'enabled': True, 'port': 15433,
                         'source': '198.51.100.0/24',
                         'host': 'db.example.com',
                         'private_host': '192.0.2.10'},
            public_url='db://app@db.example.com:15433/app'),
            'restart_required': True})
        columns, values = self.execute(database.ShowDatabase, ['orders'])
        result = dict(zip(columns, values))
        self.assertIs(True, result['Public'])
        self.assertEqual('198.51.100.0/24', result['Allowed from'])
        self.assertEqual('db://app@db.example.com:15433/app',
                         result['Public URL'])
        self.assertIn('restart is required', self.app.stderr.getvalue())
        self.register('PATCH', PATH, json={
            'database': dict(ROW, restart_required=True)})
        self.execute(database.SetDatabase, [IDENTITY, '--public'])
        self.assertEqual(2, self.app.stderr.getvalue().count('restart'))

    def test_database_backups_routes_and_policy_bodies(self):
        policy = {'enabled': True, 'hour': 2, 'minute': 0,
                  'keep_daily': 14, 'keep_weekly': 8}
        self.register('GET', PATH + '/backup_policy',
                      json={'backup_policy': policy})
        self.register('PUT', PATH + '/backup_policy',
                      json={'backup_policy': policy})
        self.register('POST', PATH + '/backups',
                      json={'backup': {'id': 'backup', 'status': 'PENDING'}})
        self.register('GET', PATH + '/backups', json={'backups': [
            {'id': 'backup', 'status': 'ACTIVE', 'kind': 'manual'}]})
        self.execute(database.ShowDatabaseBackupPolicy, ['orders'])
        self.assert_request('GET', PATH + '/backup_policy', version='1.29')
        self.execute(database.SetDatabaseBackupPolicy, [
            'orders', '--enable', '--hour', '2', '--minute', '0',
            '--keep-daily', '14', '--keep-weekly', '8'])
        self.assert_request('PUT', PATH + '/backup_policy',
                            {'backup_policy': policy}, version='1.29')
        self.execute(database.SetDatabaseBackupPolicy, [IDENTITY, '--disable'])
        self.assert_request('PUT', PATH + '/backup_policy',
                            {'backup_policy': {'enabled': False}},
                            version='1.29')
        self.execute(database.CreateDatabaseBackup, ['orders'])
        self.assert_request('POST', PATH + '/backups', {}, version='1.29')
        columns, rows = self.execute(database.ListDatabaseBackup, ['orders'])
        self.assertEqual('backup', list(rows)[0][0])
        self.assert_request('GET', PATH + '/backups', version='1.29')
        self.assertRaisesRegex(exceptions.CommandError, 'Nothing to change',
                               self.execute, database.SetDatabaseBackupPolicy,
                               [IDENTITY])
        self.assertFalse(any('/v1/services/' in r.url
                             for r in self.http.request_history))

    def test_backup_unsupported_and_missing_database_errors(self):
        for cls, method, suffix in (
                (database.ShowDatabaseBackupPolicy, 'GET', '/backup_policy'),
                (database.SetDatabaseBackupPolicy, 'PUT', '/backup_policy'),
                (database.CreateDatabaseBackup, 'POST', '/backups'),
                (database.ListDatabaseBackup, 'GET', '/backups')):
            flags = ['--disable'] if method == 'PUT' else []
            self.register(method, PATH + suffix, status_code=409, json={
                'faultcode': 'Client',
                'faultstring': 'Backups are not available for this engine.'})
            self.assertRaisesRegex(
                exceptions.CommandError,
                'Backups are not available for this engine', self.execute,
                cls, ['orders'] + flags)
            self.register(method, PATH + suffix, status_code=404)
            self.assertRaisesRegex(exceptions.CommandError,
                                   "Database 'orders' not found", self.execute,
                                   cls, ['orders'] + flags)

    def test_new_commands_reject_older_api_before_requests(self):
        self.select('1.28', '1.0')
        for cls, flags in (
                (database.SetDatabase, ['--public']),
                (database.UnsetDatabase, ['--cpu-limit']),
                (database.ShowDatabaseBackupPolicy, []),
                (database.SetDatabaseBackupPolicy, ['--enable']),
                (database.CreateDatabaseBackup, []),
                (database.ListDatabaseBackup, [])):
            count = len(self.http.request_history)
            self.assertRaisesRegex(exceptions.CommandError, '1.29',
                                   self.execute, cls, [IDENTITY] + flags)
            self.assertEqual(count, len(self.http.request_history))

    def test_new_parsers_reject_invalid_options(self):
        for cls, flags in (
                (database.SetDatabase, ['--public', '--no-public']),
                (database.SetDatabase, ['--public-port', '1023']),
                (database.SetDatabase, ['--public-port', '65536']),
                (database.SetDatabase, ['--allow-from', '::/0']),
                (database.SetDatabase, ['--allow-from', 'invalid']),
                (database.SetDatabase, ['--cpu-limit', '0']),
                (database.SetDatabase, ['--memory-limit-mb', '-1']),
                (database.SetDatabase, ['--engine', ENGINE['engine']]),
                (database.SetDatabase, ['--version', 'current']),
                (database.SetDatabase, ['--server', common.SERVER]),
                (database.SetDatabaseBackupPolicy, ['--enable', '--disable']),
                (database.SetDatabaseBackupPolicy, ['--engine', 'anything']),
                (database.SetDatabaseBackupPolicy,
                 ['--container', 'anything']),
                (database.SetDatabaseBackupPolicy, ['--keep-daily', '-1'])):
            cmd = cls(self.app, None)
            with mock.patch('sys.stderr', new=io.StringIO()):
                self.assertRaises(SystemExit,
                                  cmd.get_parser('test').parse_args,
                                  [IDENTITY] + flags)

    def test_shared_backup_commands_accept_database_backups(self):
        backup = {'id': common.SERVER, 'database_id': IDENTITY,
                  'status': 'ACTIVE'}
        path = 'backups/' + common.SERVER
        self.register('GET', path, json={'backup': backup})
        columns, values = self.execute(catchup.BackupShow, [common.SERVER])
        self.assertEqual(IDENTITY, dict(zip(columns, values))['Database ID'])
        self.register('POST', path + '/restore', status_code=202,
                      json={'restore': {'database_id': IDENTITY}})
        self.execute(catchup.BackupRestore,
                     [common.SERVER, '--name', 'orders-restored'])
        self.assert_request('POST', path + '/restore',
                            {'name': 'orders-restored'})
        self.register('DELETE', path, status_code=202)
        self.execute(catchup.BackupDelete, [common.SERVER])
        self.assert_request('DELETE', path)

        self.register('GET', path + '/download',
                      content=gzip.compress(b'dump'))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'database.dump.gz'
            self.execute(catchup.BackupDownload,
                         [common.SERVER, '--file', str(target)])
            self.assertEqual(b'dump', gzip.decompress(target.read_bytes()))
        self.assert_request('GET', path + '/download')
