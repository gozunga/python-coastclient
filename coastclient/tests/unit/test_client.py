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

"""Transport contract tests use real sessions and mocked HTTP only."""
import logging
from types import SimpleNamespace

import fixtures
from keystoneauth1 import exceptions
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from oslotest import base
import requests
from requests_mock.contrib import fixture

from coastclient.osc import plugin
from coastclient.v1 import client

AUTH_URL = 'https://identity.example.com/v3'
ROOT_URL = 'https://app.example.com/'
V1_URL = ROOT_URL + 'v1/'


class ClientTest(base.BaseTestCase):

    def setUp(self):
        super().setUp()
        self.http = self.useFixture(fixture.Fixture())
        self.token = ks_fixture.V3Token(project_id='project-id')
        self.token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region-one', url=ROOT_URL)
        self.http.post(AUTH_URL + '/auth/tokens', json=self.token,
                       headers={'X-Subject-Token': 'test-token'})
        self.auth = v3.Password(
            auth_url=AUTH_URL, username='user', password='test-password',
            user_domain_name='Default', project_id='project-id')
        self.session = session.Session(
            auth=self.auth, verify='/example/ca.pem',
            cert=('/example/client.pem', '/example/client.key'),
            connect_retries=3)
        self.discovery()

    def discovery(self, catalog_url=ROOT_URL, minimum='1.0', maximum='1.1',
                  major='v1'):
        version = {'id': major, 'status': 'CURRENT',
                   'min_version': minimum, 'max_version': maximum,
                   'links': [{'rel': 'self', 'href': V1_URL}]}
        body = ({'version': version} if catalog_url == V1_URL
                else {'versions': [version]})
        self.http.get(catalog_url, json=body)

    def make_client(self, **kwargs):
        kwargs.setdefault('microversion', '1.0')
        return client.Client(self.session, region_name='region-one', **kwargs)

    def test_root_discovery_and_resource_response(self):
        api = self.make_client()
        self.http.get(V1_URL + 'stacks', json={'stacks': [{'id': 'one'}]})
        response = api.request('/stacks', 'GET')
        self.assertEqual({'stacks': [{'id': 'one'}]}, response.json())
        request = self.http.last_request
        self.assertEqual(V1_URL + 'stacks', request.url)
        self.assertEqual('test-token', request.headers['X-Auth-Token'])
        self.assertEqual('app-hosting 1.0',
                         request.headers[
                             'OpenStack-API-Version'])
        discovery = [r for r in self.http.request_history
                     if r.url == ROOT_URL][0]
        self.assertNotIn('X-Auth-Token', discovery.headers)
        self.assertIs(self.session, api.adapter.session)
        self.assertEqual('/example/ca.pem', request.verify)
        self.assertEqual(('/example/client.pem', '/example/client.key'),
                         request.cert)

    def test_versioned_catalog_has_no_double_v1(self):
        self.token['token']['catalog'][0]['endpoints'][0]['url'] = V1_URL
        self.discovery(catalog_url=V1_URL)
        api = self.make_client(microversion='1.1')
        self.http.get(V1_URL + 'stacks', json={'stacks': []})
        api.request('/stacks', 'GET')
        self.assertEqual(V1_URL + 'stacks', self.http.last_request.url)
        self.assertEqual('app-hosting 1.1',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])

    def test_application_credentials_use_same_session(self):
        self.session.auth = v3.ApplicationCredential(
            auth_url=AUTH_URL, application_credential_id='credential-id',
            application_credential_secret='credential-secret')
        api = self.make_client()
        self.http.get(V1_URL + 'stacks', json={'stacks': []})
        api.request('/stacks', 'GET')
        auth_request = [r for r in self.http.request_history
                        if r.url.endswith('/auth/tokens')][0]
        credentials = auth_request.json()['auth']['identity']
        self.assertEqual(['application_credential'], credentials['methods'])
        self.assertEqual('credential-id',
                         credentials['application_credential']['id'])
        self.assertEqual('test-token',
                         self.http.last_request.headers['X-Auth-Token'])

    def test_catalog_region_and_interface_selection(self):
        self.token.add_service('compute', name='nova').add_endpoint(
            interface='public', region='region-one',
            url='https://compute.example.com/')
        self.token['token']['catalog'][0]['endpoints'].append({
            'interface': 'internal', 'region': 'region-two',
            'url': 'https://internal.example.com/'})
        self.discovery(catalog_url='https://internal.example.com/')
        api = client.Client(self.session, region_name='region-two',
                            interface='internal')
        self.assertEqual('https://internal.example.com/v1/',
                         api.adapter.endpoint_override)
        urls = [r.url for r in self.http.request_history]
        self.assertIn('https://internal.example.com/', urls)
        self.assertNotIn(ROOT_URL, urls)
        self.assertNotIn('https://compute.example.com/', urls)

    def test_missing_catalog_does_not_use_default_endpoint(self):
        self.token['token']['catalog'] = []
        self.assertRaises(exceptions.EndpointNotFound, self.make_client)
        self.assertFalse(any(r.url.startswith(ROOT_URL)
                             for r in self.http.request_history))

    def test_invalid_microversions_fail_before_auth(self):
        for version in ('latest', '1', '1.32', '2.0', ''):
            self.assertRaises(ValueError, self.make_client,
                              microversion=version)
        self.assertEqual([], self.http.request_history)

    def test_bounds_reject_instead_of_downgrade(self):
        self.discovery(maximum='1.0')
        self.assertRaises(exceptions.DiscoveryFailure,
                          self.make_client, microversion='1.1')
        self.assertFalse(any('/stacks' in r.url
                             for r in self.http.request_history))

    def test_bounds_reject_below_minimum(self):
        self.discovery(minimum='1.1')
        self.assertRaises(exceptions.DiscoveryFailure, self.make_client)

    def test_missing_bounds_fail_closed(self):
        self.discovery(minimum='', maximum='')
        self.assertRaises(exceptions.DiscoveryFailure, self.make_client)

    def test_other_major_is_not_selected(self):
        self.discovery(major='v2')
        self.assertRaises(exceptions.DiscoveryFailure, self.make_client)

    def test_invalid_discovery_does_not_guess_version_path(self):
        self.http.get(ROOT_URL, json={'not-versions': []})
        self.assertRaises(exceptions.DiscoveryFailure, self.make_client)
        self.assertNotIn(V1_URL, [r.url for r in self.http.request_history])

    def test_quota_selected_version_allowed(self):
        api = self.make_client(microversion='1.1')
        self.http.get(V1_URL + 'quotas', json={'quotas': []})
        self.assertEqual({'quotas': []}, api.request('/quotas', 'GET').json())

    def test_quota_10_allowed(self):
        api = self.make_client()
        self.http.get(V1_URL + 'quotas/project-id', json={'quota': {}})
        self.assertEqual({'quota': {}},
                         api.request('/quotas/project-id', 'GET').json())

    def test_header_cannot_override_selection(self):
        api = self.make_client()
        self.http.get(V1_URL + 'stacks', json={'stacks': []})
        api.request('/stacks', 'GET', microversion='1.1',
                    headers={'openstack-api-version': 'app-hosting latest'})
        self.assertEqual('app-hosting 1.0',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])

    def test_domain_tls_microversion_is_one_call(self):
        api = self.make_client()
        path = '/services/11111111-1111-4111-8111-111111111111/domains'
        self.http.post(V1_URL + path[1:], json={'domain': {}})
        api.request(path, 'POST', json={'domain': {'tls': 'none'}},
                    domain_tls_microversion='1.2')
        self.assertEqual('app-hosting 1.2',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])
        self.assertEqual('1.0', api.microversion)
        before = len(self.http.request_history)
        self.assertRaises(ValueError, api.request, '/stacks', 'GET',
                          domain_tls_microversion='1.2')
        self.assertRaises(ValueError, api.request, path, 'POST',
                          domain_tls_microversion='1.1')
        self.assertEqual(before, len(self.http.request_history))

    def test_http_errors_preserved(self):
        api = self.make_client()
        for status, exception in (
                (401, exceptions.Unauthorized), (403, exceptions.Forbidden),
                (404, exceptions.NotFound), (409, exceptions.Conflict),
                (406, exceptions.NotAcceptable)):
            self.http.get(V1_URL + 'stacks', status_code=status,
                          json={'error': {'message': 'Rejected'}})
            self.assertRaises(exception, api.request, '/stacks', 'GET')

    def test_uncertain_post_not_retried(self):
        api = self.make_client()
        for outcome, error in (
                ({'exc': requests.exceptions.ConnectionError},
                 exceptions.connection.ConnectFailure),
                ({'status_code': 503}, exceptions.ServiceUnavailable)):
            self.http.post(V1_URL + 'stacks', **outcome)
            before = len(self.http.request_history)
            self.assertRaises(error, api.request, '/stacks', 'POST',
                              json={'stack': {'name': 'test'}})
            self.assertEqual(1, len(self.http.request_history) - before)

    def test_accepted_response_is_not_polled(self):
        api = self.make_client()
        self.http.post(V1_URL + 'stacks', status_code=202,
                       json={'stack': {'status': 'CREATING'}})
        response = api.request('/stacks', 'POST', json={'stack': {}})
        self.assertEqual(202, response.status_code)
        self.assertEqual('CREATING', response.json()['stack']['status'])
        self.assertEqual('POST', self.http.last_request.method)

    def test_version_relative_paths_only(self):
        api = self.make_client()
        for path in ('/v1', '/v1?query=value', '/v1/stacks',
                     '//other.example.com',
                     'https://other.example.com/stacks'):
            self.assertRaises(ValueError, api.request, path, 'GET')

    def test_make_client_uses_osc_session_and_config(self):
        instance = SimpleNamespace(
            _api_version={'coast': '1'}, session=self.session,
            region_name='region-one', interface='public',
            get_configuration=lambda: {'coast_api_microversion': '1.1'})
        api = plugin.make_client(instance)
        self.http.get(V1_URL + 'stacks', json={'stacks': [{'id': 'one'}]})
        self.assertEqual({'stacks': [{'id': 'one'}]},
                         api.request('/stacks', 'GET').json())
        self.assertIs(self.session, api.adapter.session)
        self.assertEqual('app-hosting 1.1',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])

    def test_resource_payloads_are_not_logged(self):
        api = self.make_client()
        logs = self.useFixture(fixtures.FakeLogger(
            name='keystoneauth.session', level=logging.DEBUG))
        self.http.patch(V1_URL + 'services/id/env', json={
            'env': {'KEY': 'response-secret'}})
        api.request('/services/id/env', 'PATCH', log=True,
                    json={'env': {'KEY': 'request-secret'}})
        self.assertNotIn('request-secret', logs.output)
        self.assertNotIn('response-secret', logs.output)
        self.assertNotIn('test-token', logs.output)

    def test_token_authentication_uses_osc_session(self):
        self.session.auth = v3.Token(
            auth_url=AUTH_URL, token='input-token', project_id='project-id')
        api = self.make_client()
        self.http.get(V1_URL + 'stacks', json={'stacks': []})
        api.request('/stacks', 'GET')
        auth_request = [r for r in self.http.request_history
                        if r.url.endswith('/auth/tokens')][0]
        identity = auth_request.json()['auth']['identity']
        self.assertEqual(['token'], identity['methods'])
        self.assertEqual('input-token', identity['token']['id'])
        self.assertEqual('test-token',
                         self.http.last_request.headers['X-Auth-Token'])

    def test_insecure_session_setting_is_preserved(self):
        self.session.verify = False
        api = self.make_client()
        self.http.get(V1_URL + 'stacks', json={'stacks': []})
        api.request('/stacks', 'GET')
        self.assertIs(False, self.http.last_request.verify)

    def test_health_check_microversion_is_one_call(self):
        for selected in ('1.0', '1.1'):
            api = self.make_client(microversion=selected)
            self.http.post(V1_URL + 'services', json={'service': {}})
            self.http.get(V1_URL + 'services', json={'services': []})
            body = {'service': {'health_check': 'none'}}
            api.request('/services', 'POST', json=body,
                        health_check_microversion='1.4')
            self.assertEqual(body, self.http.last_request.json())
            self.assertEqual('app-hosting 1.4',
                             self.http.last_request.headers[
                                 'OpenStack-API-Version'])
            api.request('/services', 'GET')
            self.assertEqual('app-hosting ' + selected,
                             self.http.last_request.headers[
                                 'OpenStack-API-Version'])
            self.assertEqual(selected, api.microversion)

    def test_health_check_override_rejects_other_routes_and_versions(self):
        api = self.make_client()
        before = len(self.http.request_history)
        for path, method, version in (
                ('/stacks', 'POST', '1.4'),
                ('/services', 'GET', '1.4'),
                ('/services/id/domains', 'PATCH', '1.4'),
                ('/services', 'POST', '1.3')):
            self.assertRaises(ValueError, api.request, path, method,
                              health_check_microversion=version)
        self.assertEqual(before, len(self.http.request_history))

    def test_microversions_through_19_and_older_server_bounds(self):
        self.discovery(maximum='1.9')
        for version in client.MICROVERSIONS[:10]:
            api = self.make_client(microversion=version)
            self.http.get(V1_URL + 'stacks', json={'stacks': []})
            api.request('/stacks', 'GET')
            self.assertEqual('app-hosting ' + version,
                             self.http.last_request.headers[
                                 'OpenStack-API-Version'])
        self.discovery(maximum='1.8')
        self.assertRaises(exceptions.DiscoveryFailure, self.make_client,
                          microversion='1.9')
        self.discovery(maximum='1.7')
        self.assertRaises(exceptions.DiscoveryFailure, self.make_client,
                          microversion='1.8')

    def test_console_microversion_is_one_call(self):
        app = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        for selected in ('1.0', '1.1'):
            api = self.make_client(microversion=selected)
            for path in ('/apps/%s/console' % app,
                         '/services/%s/console' % app):
                self.http.post(V1_URL + path[1:],
                               json={'url': 'wss://console.example/'})
                api.request(path, 'POST', console_microversion='1.9')
                self.assertFalse(self.http.last_request.body)
                self.assertEqual('app-hosting 1.9',
                                 self.http.last_request.headers[
                                     'OpenStack-API-Version'])
                self.assertEqual(selected, api.microversion)
        self.http.post(V1_URL + 'services/%s/console' % app,
                       json={'url': 'wss://console.example/'})
        api.request('/services/%s/console' % app, 'POST',
                    json={'command': ['ls', '-l']},
                    console_microversion='1.9')
        self.assertEqual({'command': ['ls', '-l']},
                         self.http.last_request.json())
        self.http.get(V1_URL + 'stacks', json={'stacks': []})
        api.request('/stacks', 'GET')
        self.assertEqual('app-hosting ' + selected,
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])

    def test_console_override_rejects_other_routes_and_versions(self):
        api = self.make_client()
        before = len(self.http.request_history)
        app = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        for path, method, version in (
                ('/apps/%s/console' % app, 'GET', '1.9'),
                ('/apps/%s/console' % app, 'POST', '1.8'),
                ('/stacks', 'POST', '1.9'),
                ('/apps/%s/logs' % app, 'POST', '1.9'),
                ('/apps/console', 'POST', '1.9'),
                ('/services/%s/console' % app, 'PUT', '1.9')):
            self.assertRaises(ValueError, api.request, path, method,
                              console_microversion=version)
        self.assertEqual(before, len(self.http.request_history))

    def test_default_selects_newest_common_microversion(self):
        for minimum, maximum, expected in (
                ('1.0', '1.24', '1.24'), ('1.0', '1.25', '1.25'),
                ('1.0', '1.9', '1.9'),
                ('1.0', '1.28', '1.28'), ('1.23', '1.99', '1.31'),
                ('1.0', '1.0', '1.0')):
            self.discovery(minimum=minimum, maximum=maximum)
            api = client.Client(self.session, region_name='region-one')
            self.assertEqual(expected, api.microversion)
            self.http.get(V1_URL + 'stacks', json={'stacks': []})
            api.request('/stacks', 'GET')
            self.assertEqual('app-hosting ' + expected,
                             self.http.last_request.headers[
                                 'OpenStack-API-Version'])

    def test_no_common_microversion_fails_before_resource_calls(self):
        self.discovery(minimum='1.32', maximum='1.99')
        self.assertRaises(exceptions.DiscoveryFailure, client.Client,
                          self.session, region_name='region-one')

    def test_all_supported_explicit_versions(self):
        self.discovery(maximum='1.31')
        for version in client.MICROVERSIONS:
            api = self.make_client(microversion=version)
            self.assertEqual(version, api.microversion)

    def test_feature_flags_do_not_downgrade_newest_selection(self):
        self.discovery(maximum='1.25')
        api = client.Client(self.session, region_name='region-one')
        self.http.patch(V1_URL + 'services/id', json={'service': {}})
        api.request('/services/id', 'PATCH', health_check_microversion='1.4')
        self.assertEqual('app-hosting 1.25',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])

    def test_client_fault_messages_and_server_proxy_redaction(self):
        from coastclient.osc.v1 import utils
        from osc_lib import exceptions as osc_exc
        api = self.make_client()
        for status, body, expected in (
                (409, {'faultcode': 'Client', 'faultstring':
                       'Domain check in progress since 2026-10-19'},
                 'Domain check in progress since 2026-10-19'),
                (409, {'faultcode': 'Client', 'faultstring':
                       'Pick a main domain first'},
                 'Pick a main domain first'),
                (400, {'faultcode': 'Client', 'faultstring':
                       'Invalid password=private'}, 'Invalid password=***'),
                (503, {'faultcode': 'Client', 'faultstring': 'private'},
                 'Service Unavailable'),
                (500, {'faultcode': 'Server', 'faultstring': 'private'},
                 'Request failed'),
                (409, {'error': {'message': 'private'}}, 'Conflict'),
                (400, [], 'Bad Request')):
            self.http.get(V1_URL + 'stacks', status_code=status, json=body)
            error = self.assertRaises(osc_exc.CommandError, utils.request,
                                      api, '/stacks', 'GET')
            self.assertIn(expected, str(error))
            self.assertNotIn('private', str(error))
            self.assertIn('HTTP ' + str(status), str(error))
