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

"""Ingress commands through the authenticated adapter and OSC formatters."""
import io
import json
from types import SimpleNamespace

from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from osc_lib import exceptions
from oslotest import base
from requests_mock.contrib import fixture

from coastclient.osc.v1 import ingress
from coastclient.tests.unit import labels
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase6
from coastclient.v1 import client

STACK = test_commands.STACK
RULE = test_commands.SERVER
SCOPE = ['--stack', STACK]
PATH = test_commands.STACK_PATH + '/ingress_rules'
CUSTOM = dict(id=RULE, protocol='tcp', port_range_min=8080,
              port_range_max=8080, source='0.0.0.0/0', description='',
              managed=False)
MANAGED = dict(CUSTOM, id='managed-http', port_range_min=80,
               port_range_max=80, description='Managed by Coast', managed=True)


class IngressTest(base.BaseTestCase):
    execute = test_commands.CommandTest.execute
    register = test_commands.CommandTest.register
    assert_request = test_commands.CommandTest.assert_request
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)

    def setUp(self):
        super().setUp()
        self.http = self.useFixture(fixture.Fixture())
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
            'max_version': '1.25', 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.api = client.Client(self.session, region_name='region')
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO())

    def assert_stack_name_route(self, cls, extra, method, path, body):
        lookup = self.register('GET', 'stacks', json={'stacks': [
            {'id': test_commands.APPGROUP, 'name': 'shop-prefix'},
            {'id': STACK, 'name': 'shop'}]})
        response = {'json': body} if body else {'status_code': 204}
        self.register(method, path, **response)
        self.execute(cls, ['--stack', 'shop', *extra])
        self.assertEqual(1, lookup.call_count)
        payload = ({'ingress_rule': {'protocol': 'icmp'}}
                   if method == 'POST' else None)
        self.assert_request(method, path, payload, version='1.25')

    def test_help_explains_api_source_default_and_repair_timing(self):
        for cls in (ingress.CreateIngressRule, ingress.DeleteIngressRule):
            help_text = ' '.join(cls(self.app, None).get_parser(
                'test').format_help().split())
            self.assertIn('next enabled repair sweep', help_text)
            if cls is ingress.CreateIngressRule:
                self.assertIn('API default: 0.0.0.0/0', help_text)

    def test_list_resolves_stack_name(self):
        self.assert_stack_name_route(
            ingress.ListIngressRule, [], 'GET', PATH,
            {'ingress_rules': [MANAGED, CUSTOM]})

    def test_create_resolves_stack_name(self):
        self.assert_stack_name_route(
            ingress.CreateIngressRule, ['--protocol', 'icmp'], 'POST', PATH,
            {'ingress_rule': CUSTOM})

    def test_delete_resolves_stack_name(self):
        self.assert_stack_name_route(
            ingress.DeleteIngressRule, [RULE], 'DELETE', PATH + '/' + RULE,
            None)

    def test_stack_name_lookup_failures_do_not_request_ingress_routes(self):
        for cls, verb, extra in (
                (ingress.ListIngressRule, 'list', []),
                (ingress.CreateIngressRule, 'create', ['--protocol', 'icmp']),
                (ingress.DeleteIngressRule, 'delete', [RULE])):
            for rows, message in (
                    ([], "Stack 'shop' not found."),
                    ([{'id': STACK, 'name': 'shop'},
                      {'id': test_commands.APPGROUP, 'name': 'shop'}],
                     "More than one stack is named 'shop'; use the ID.")):
                with self.subTest(command=verb, message=message):
                    lookup = self.register('GET', 'stacks',
                                           json={'stacks': rows})
                    before = len(self.http.request_history)
                    error = self.assertRaises(
                        exceptions.CommandError, self.execute, cls,
                        ['--stack', 'shop', *extra])
                    self.assertEqual(message, str(error))
                    result, _app = test_phase6.Phase6Test.run_shell(self, [
                        '--os-coast-api-microversion', '1.25',
                        'coast', 'stack', 'ingress', 'rule', verb,
                        '--stack', 'shop', *extra])
                    self.assertEqual(1, result)
                    self.assertEqual(2, lookup.call_count)
                    calls = self.http.request_history[before:]
                    self.assertFalse(any('/ingress_rules' in req.url
                                         for req in calls))

    def test_list_json_preserves_managed_and_order(self):
        self.register('GET', PATH, json={'ingress_rules': [MANAGED, CUSTOM]})
        self.execute(ingress.ListIngressRule, [*SCOPE, '-f', 'json'], True)
        rows = json.loads(self.app.stdout.getvalue())
        self.assertEqual(
            {'ID', 'Protocol', 'Port range min', 'Port range max',
             'Source', 'Description', 'Managed'}, set(rows[0]))
        self.assertEqual([80, 8080], [r['Port range min'] for r in rows])
        self.assertEqual([80, 8080], [r['Port range max'] for r in rows])
        self.assertEqual(['managed-http', RULE], [r['ID'] for r in rows])
        self.assertEqual([True, False], [r['Managed'] for r in rows])
        self.assert_request('GET', PATH, version='1.25')

    def test_empty_list(self):
        self.register('GET', PATH, json={'ingress_rules': []})
        columns, rows = self.execute(ingress.ListIngressRule, [*SCOPE])
        self.assertIn('Managed', columns)
        self.assertEqual([], list(rows))

    def test_create_payloads_and_json(self):
        for protocol, port, limits in (
                ('tcp', '8080', (8080, 8080)),
                ('udp', '1000:2000', (1000, 2000)),
                ('tcp', '1:65535', (1, 65535)),
                ('icmp', None, None)):
            argv = [*SCOPE, '--protocol', protocol, '--source',
                    '192.0.2.0/24', '--description', 'Example rule']
            values = dict(protocol=protocol, source='192.0.2.0/24',
                          description='Example rule')
            if port:
                argv.extend(['--port', port])
                values.update(port_range_min=limits[0],
                              port_range_max=limits[1])
            result = dict(CUSTOM, **values)
            self.register('POST', PATH, status_code=201,
                          json={'ingress_rule': result})
            self.app.stdout = io.StringIO()
            self.execute(ingress.CreateIngressRule,
                         [*argv, '-f', 'json'], True)
            self.assert_request('POST', PATH, {'ingress_rule': values},
                                version='1.25')
            self.assertEqual(labels.labelled(result),
                             json.loads(self.app.stdout.getvalue()))

    def test_source_description_defaults_left_to_api(self):
        for protocol, extra, values in (
                ('icmp', [], {'protocol': 'icmp'}),
                ('tcp', ['--port', '80'], dict(
                    protocol='tcp', port_range_min=80, port_range_max=80))):
            self.register('POST', PATH, status_code=201,
                          json={'ingress_rule': CUSTOM})
            self.execute(ingress.CreateIngressRule,
                         [*SCOPE, '--protocol', protocol, *extra])
            self.assert_request('POST', PATH, {'ingress_rule': values},
                                version='1.25')

    def test_delete_204_is_silent(self):
        self.register('DELETE', PATH + '/' + RULE, status_code=204)
        self.execute(ingress.DeleteIngressRule, [*SCOPE, RULE])
        self.assert_request('DELETE', PATH + '/' + RULE, version='1.25')
        self.assertEqual('', self.app.stdout.getvalue())

    def test_invalid_arguments_do_not_send_requests(self):
        before = len(self.http.request_history)
        for argv in ([*SCOPE], [*SCOPE, '--protocol', 'sctp']):
            self.assertRaises(SystemExit, self.execute,
                              ingress.CreateIngressRule, argv)
        for port in ('0', '65536', '2:1', '1:2:3', 'abc', ':80',
                     '80:', '-1', '1.5'):
            self.assertRaises(SystemExit, self.execute,
                              ingress.CreateIngressRule,
                              [*SCOPE, '--protocol', 'tcp', '--port', port])
        for protocol, extra in (('tcp', []), ('udp', []),
                                ('icmp', ['--port', '80'])):
            self.assertRaises(exceptions.CommandError, self.execute,
                              ingress.CreateIngressRule,
                              [*SCOPE, '--protocol', protocol, *extra])
        self.assertRaises(SystemExit, self.execute, ingress.DeleteIngressRule,
                          [*SCOPE, '../other'])
        self.assertEqual(before, len(self.http.request_history))

    def test_older_microversion_rejected_without_resource_call(self):
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.24')
        before = len(self.http.request_history)
        for cls, argv in (
                (ingress.ListIngressRule, [*SCOPE]),
                (ingress.CreateIngressRule, [*SCOPE, '--protocol', 'icmp']),
                (ingress.DeleteIngressRule, [*SCOPE, RULE])):
            error = self.assertRaises(exceptions.CommandError,
                                      self.execute, cls, argv)
            self.assertIn('1.25', str(error))
        self.assertEqual(before, len(self.http.request_history))

    def test_api_client_errors_are_preserved(self):
        for cls, argv, method, path in (
                (ingress.CreateIngressRule, [*SCOPE, '--protocol', 'icmp'],
                 'POST', PATH),
                (ingress.DeleteIngressRule, [*SCOPE, RULE],
                 'DELETE', PATH + '/' + RULE)):
            for status, message in (
                    (400, 'Source must be an IPv4 CIDR.'),
                    (401, 'Authentication required.'),
                    (403, 'Quota exceeded for ingress_rules.'),
                    (404, 'Ingress rule not found.'),
                    (409, 'Ingress rule already exists.'),
                    (409, 'Stack busy.'),
                    (413, 'Ingress rule limit exceeded.')):
                self.register(method, path, status_code=status, json={
                    'faultcode': 'Client', 'faultstring': message})
                before = len(self.http.request_history)
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls, argv)
                self.assertIn('not found' if status == 404 else message,
                              str(error))
                self.assertIn(
                    'not found' if status == 404 else 'HTTP %s' % status,
                    str(error))
                if status != 401:
                    self.assertEqual(
                        1, len(self.http.request_history) - before)

    def test_managed_delete_reaches_api(self):
        for rule in ('managed-http', 'managed-https', 'managed-ssh'):
            self.register('DELETE', PATH + '/' + rule, status_code=409,
                          json={'faultcode': 'Client', 'faultstring':
                                'Coast managed rules are required.'})
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      ingress.DeleteIngressRule,
                                      [*SCOPE, rule])
            self.assertIn('Coast managed rules are required.', str(error))
            self.assert_request('DELETE', PATH + '/' + rule, version='1.25')

    def test_installed_shell_commands_and_errors(self):
        for verb, argv, method, path, status, body in (
                ('list', [*SCOPE], 'GET', PATH, 200,
                 {'ingress_rules': [MANAGED, CUSTOM]}),
                ('create', [*SCOPE, '--protocol', 'icmp'], 'POST', PATH, 201,
                 {'ingress_rule': CUSTOM}),
                ('delete', [*SCOPE, RULE], 'DELETE', PATH + '/' + RULE,
                 204, None)):
            self.register(method, path, status_code=status,
                          **({'json': body} if body else {}))
            formatter = ['-f', 'json'] if verb != 'delete' else []
            result, app = test_phase6.Phase6Test.run_shell(self, [
                '--os-coast-api-microversion', '1.25',
                'coast', 'stack', 'ingress', 'rule', verb, *argv, *formatter])
            self.assertEqual(0, result)
            if verb == 'delete':
                self.assertEqual('', app.stdout.getvalue())
            else:
                json.loads(app.stdout.getvalue())
            self.assert_request(method, path,
                                {'ingress_rule': {'protocol': 'icmp'}}
                                if verb == 'create' else None,
                                version='1.25')
            if verb != 'list':
                self.register(method, path, status_code=409, json={
                    'faultcode': 'Client', 'faultstring':
                    'Coast managed rules are required.'})
                result, app = test_phase6.Phase6Test.run_shell(self, [
                    '--os-coast-api-microversion', '1.25',
                    'coast', 'stack', 'ingress', 'rule', verb, *argv])
                self.assertNotEqual(0, result)
