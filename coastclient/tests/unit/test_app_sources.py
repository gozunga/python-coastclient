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

"""M5 app source commands with authenticated, mocked HTTP requests."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import fixtures
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from osc_lib import exceptions
from oslotest import base
from requests_mock.contrib import fixture

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import service
from coastclient.tests.unit import test_phase4 as common
from coastclient.tests.unit import test_service_targets as targets
from coastclient.v1 import client


PUBLIC = {'public_key': 'ssh-ed25519 fake-public',
          'fingerprint': 'SHA256:fake-fingerprint'}
ROW = {'id': common.SERVICE, 'source': {'type': 'git', 'deploy_key': PUBLIC}}
REPLACEMENT = {'public_key': 'ssh-ed25519 replacement-public',
               'fingerprint': 'SHA256:replacement-fingerprint'}
ROTATED = {'id': common.SERVICE,
           'source': {'type': 'git', 'deploy_key': REPLACEMENT}}


class AppSourcesTest(base.BaseTestCase):
    execute = targets.ServiceTargetTest.execute
    register = targets.ServiceTargetTest.register
    assert_request = targets.ServiceTargetTest.assert_request

    def setUp(self):
        super().setUp()
        self.input = io.StringIO('private-value')
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        self.http = self.useFixture(fixture.Fixture())
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
            'max_version': '1.1',
            'links': [{'rel': 'self', 'href': common.BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.0')
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO())
        self.app.stderr = io.StringIO()
        self.select()

    def select(self, maximum='1.31', selected='1.0'):
        self.http.get(common.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': maximum,
            'links': [{'rel': 'self', 'href': common.BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion=selected)
        self.app.client_manager.coast = self.api

    def create(self, options):
        return self.execute(service.CreateApp, common.CREATE[:-2] + options)

    def test_generated_key_create_and_hint(self):
        self.register('POST', 'services', json={'service': ROW},
                      status_code=202)
        self.create(['--repo-url', 'git@example.com:org/app.git',
                     '--deploy-key', 'generate'])
        values = dict(common.BODY['service'], deploy=False, source={
            'type': 'git', 'repo_url': 'git@example.com:org/app.git',
            'deploy_key': {'generate': True}})
        self.assert_request('POST', 'services', {'service': values}, '1.30')
        self.assertIn(PUBLIC['public_key'], self.app.stderr.getvalue())
        self.assertIn('openstack coast deploy web', self.app.stderr.getvalue())
        self.assertEqual('1.0', self.api.microversion)

    def test_private_key_upload_and_rotation(self):
        for path_kind in ('stdin', 'file'):
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'fake-key'
                path.write_text('fake-private', encoding='utf-8')
                self.input = io.StringIO('fake-private')
                self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
                filename = '-' if path_kind == 'stdin' else str(path)
                self.register('POST', 'services', json={'service': ROW})
                self.create(['--repo-url', 'ssh://git@example.com/org/app',
                             '--deploy-key-file', filename])
                self.assertEqual({'private_key': 'fake-private'},
                                 self.http.last_request.json()[
                                     'service']['source']['deploy_key'])
                self.register('GET', common.PATH, response_list=[
                    {'json': {'service': ROW}},
                    {'json': {'service': ROTATED}}])
                self.register('PATCH', common.PATH, json={'service': ROW})
                self.execute(service.SetApp, [
                    common.SERVICE, '--deploy-key-file', filename])
                request = [req for req in self.http.request_history
                           if req.method == 'PATCH'][-1]
                self.assertEqual({
                    'service': {'source': {'deploy_key': {
                        'private_key': 'fake-private'}}}}, request.json())
                self.assertIn(REPLACEMENT['public_key'],
                              self.app.stderr.getvalue())
                self.assertNotIn('fake-private', self.app.stdout.getvalue())
                self.assertNotIn('fake-private', self.app.stderr.getvalue())

    def test_generated_key_rotation(self):
        self.register('GET', common.PATH, response_list=[
            {'json': {'service': ROW}},
            {'json': {'service': ROW}},
            {'json': {'service': ROTATED}}])
        self.register('PATCH', common.PATH, json={'service': ROW})
        with mock.patch.object(service.time, 'sleep') as sleep:
            columns, values = self.execute(service.SetApp, [
                common.SERVICE, '--deploy-key', 'generate'])
        sleep.assert_called_once_with(2)
        self.assert_request('GET', common.PATH, version='1.30')
        request = [req for req in self.http.request_history
                   if req.method == 'PATCH'][-1]
        self.assertEqual({'service': {
            'source': {'deploy_key': {'generate': True}}}}, request.json())
        self.assertIn(REPLACEMENT['public_key'], self.app.stderr.getvalue())
        self.assertIn('Add this deploy key', self.app.stderr.getvalue())
        self.assertNotIn(PUBLIC['public_key'], self.app.stderr.getvalue())
        self.assertNotIn(PUBLIC['public_key'], str(values))

    def test_key_rotation_still_pending(self):
        for options in (['--deploy-key', 'generate'],
                        ['--deploy-key-file', '-']):
            self.app.stderr = io.StringIO()
            self.register('GET', common.PATH, json={'service': ROW})
            self.register('PATCH', common.PATH, json={'service': ROW})
            with mock.patch.object(service.time, 'monotonic',
                                   side_effect=[0, 0, 30]), \
                    mock.patch.object(service.time, 'sleep') as sleep:
                columns, values = self.execute(
                    service.SetApp, [common.SERVICE] + options)
            sleep.assert_called_once_with(2)
            output = self.app.stderr.getvalue()
            self.assertIn('new deploy key is still being prepared', output)
            self.assertIn('openstack coast app deploy-key show ' +
                          common.SERVICE, output)
            self.assertNotIn('Add this deploy key', output)
            self.assertNotIn(PUBLIC['public_key'], output)
            self.assertNotIn(PUBLIC['public_key'], str(values))
            self.assertNotIn('private-value', str(values) + output)

    def test_file_sources(self):
        for flag, kind in (('--dockerfile', 'dockerfile'),
                           ('--compose-file', 'compose')):
            for filename in ('-', 'file'):
                with TemporaryDirectory() as directory:
                    path = Path(directory) / 'source'
                    path.write_text('fake-content', encoding='utf-8')
                    self.input = io.StringIO('fake-content')
                    self.useFixture(fixtures.MonkeyPatch(
                        'sys.stdin', self.input))
                    self.register('POST', 'services', json={'service': {
                        'id': common.SERVICE, 'source': {'type': kind}}})
                    self.create([flag, '-' if filename == '-' else str(path)])
                    values = dict(common.BODY['service'], source={
                        'type': kind, kind: 'fake-content'},
                        kind='service' if kind == 'compose' else 'app')
                    self.assert_request('POST', 'services',
                                        {'service': values}, '1.31')
                    self.assertNotIn('fake-content',
                                     self.app.stdout.getvalue())

    def test_show_public_key_and_app_fields(self):
        self.register('GET', common.PATH, json={'service': ROW})
        self.execute(service.ShowAppDeployKey, [common.SERVICE])
        self.assertEqual(PUBLIC['public_key'] + '\n',
                         self.app.stdout.getvalue())
        self.assert_request('GET', common.PATH, version='1.30')
        self.select(selected='1.30')
        self.register('GET', common.PATH + '/webhooks', status_code=403)
        columns, values = self.execute(service.ShowApp, [common.SERVICE])
        result = dict(zip(columns, values))
        self.assertEqual(PUBLIC['public_key'], result['Deploy key public key'])
        self.assertEqual(PUBLIC['fingerprint'],
                         result['Deploy key fingerprint'])

    def test_dockerfile_replace_and_show(self):
        self.input = io.StringIO('FROM scratch\n')
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        path = common.PATH + '/dockerfile'
        body = {'dockerfile': {'text': 'FROM scratch\n'}}
        self.register('PUT', path, json=body)
        columns, values = self.execute(service.SetApp, [
            common.SERVICE, '--dockerfile', '-'])
        self.assert_request('PUT', path, body, '1.31')
        self.assertNotIn('FROM scratch', str(values))
        self.register('GET', path, json=body)
        self.execute(service.ShowAppDockerfile, [common.SERVICE])
        self.assertEqual('FROM scratch\n', self.app.stdout.getvalue())
        self.assert_request('GET', path, version='1.31')

    def test_created_compose_service_show_and_set(self):
        self.select(selected='1.12')
        original = 'services:\n  web:\n    image: example/web:initial\n'
        updated = 'services:\n  web:\n    image: example/web:updated\n'
        self.input = io.StringIO(original)
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        self.register('POST', 'services', status_code=202, json={'service': {
            'id': common.SERVICE, 'kind': 'service',
            'source': {'type': 'compose'}}})
        before = len(self.http.request_history)
        columns, values = self.create(['--compose-file', '-'])
        created = dict(zip(columns, values))
        self.assertEqual(common.SERVICE, created['ID'])
        self.assertEqual('service', created['Kind'])
        self.assert_request('POST', 'services', {'service': dict(
            common.BODY['service'], kind='service',
            source={'type': 'compose', 'compose': original})}, '1.31')

        target = created['ID']
        path = 'services/' + target + '/compose'
        self.register('GET', path, json={'compose': {'yaml': original}})
        columns, values = self.execute(catchup.ComposeShow, [
            '--service', target])
        self.assertEqual({'Yaml': original}, dict(zip(columns, values)))
        self.assert_request('GET', path, version='1.12')

        self.input = io.StringIO(updated)
        self.useFixture(fixtures.MonkeyPatch('sys.stdin', self.input))
        self.register('PUT', path, json={'compose': {'yaml': updated}})
        columns, values = self.execute(catchup.ComposeSet, [
            '--service', target, '--file', '-'])
        self.assertEqual({'Saved': True}, dict(zip(columns, values)))
        self.assert_request('PUT', path, {'compose': {'yaml': updated}},
                            '1.12')
        self.assertEqual([
            ('POST', common.BASE + 'services'),
            ('GET', common.BASE + path),
            ('PUT', common.BASE + path),
        ], [(request.method, request.url)
            for request in self.http.request_history[before:]])

    def test_compose_write_does_not_echo_content(self):
        self.register('PUT', common.PATH + '/compose', json={
            'compose': {'yaml': 'fake-content'}})
        columns, values = self.execute(catchup.ComposeSet, [
            '--service', common.SERVICE, '--file', '-'])
        self.assertEqual({'Saved': True}, dict(zip(columns, values)))
        self.assert_request('PUT', common.PATH + '/compose', {
            'compose': {'yaml': 'private-value'}})

    def test_older_servers(self):
        for options, version in ((['--repo-url', 'git@example.com:org/app',
                                   '--deploy-key', 'generate'], '1.30'),
                                 (['--dockerfile', '-'], '1.31'),
                                 (['--compose-file', '-'], '1.31')):
            self.select(maximum='1.29')
            before = len(self.http.request_history)
            error = self.assertRaises(exceptions.CommandError,
                                      self.create, options)
            self.assertIn('needs API version ' + version, str(error))
            self.assertEqual(before, len(self.http.request_history))

    def test_source_conflicts(self):
        for options in (['--dockerfile', '-', '--image', 'image'],
                        ['--compose-file', '-', '--repo-url', 'url'],
                        ['--dockerfile', '-', '--compose-file', '-'],
                        ['--image', 'image', '--tag', 'tag',
                         '--deploy-key', 'generate']):
            self.assertRaises(exceptions.CommandError, self.create, options)
        cmd = service.CreateApp(self.app, None)
        self.assertRaises(SystemExit, cmd.get_parser('test').parse_args,
                          common.CREATE + ['--deploy-key', 'generate',
                                           '--deploy-key-file', '-'])

    def test_file_errors(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'missing'
            for content in (None, b'\xff'):
                if content:
                    path.write_bytes(content)
                for option in ('--dockerfile', '--compose-file'):
                    self.assertRaises(exceptions.CommandError, self.create,
                                      [option, str(path)])

    def test_api_passphrase_error_is_surfaced(self):
        self.register('POST', 'services', status_code=400, json={
            'faultcode': 'Client',
            'faultstring': 'Passphrase-protected private keys are '
                           'not supported.'})
        error = self.assertRaises(exceptions.CommandError, self.create, [
            '--repo-url', 'git@example.com:org/app', '--deploy-key-file', '-'])
        self.assertIn('Passphrase-protected', str(error))
        self.assertNotIn('private-value', str(error))

    def test_newer_selected_version_is_preserved(self):
        self.select(selected='1.31')
        self.register('GET', common.PATH, json={'service': ROW})
        self.execute(service.ShowAppDeployKey, [common.SERVICE])
        self.assert_request('GET', common.PATH, version='1.31')
