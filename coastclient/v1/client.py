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

"""Authenticated public API transport; no command or resource model."""
from keystoneauth1 import adapter
from keystoneauth1 import discover
from keystoneauth1 import exceptions

from coastclient.v1 import stacks

MICROVERSIONS = tuple('1.%d' % minor for minor in range(32))
# Container terminal. Sent only on the console POST.
CONSOLE_MICROVERSION = '1.9'
# Explicit app/service kind on create. Sent only on POST /services.
KIND_MICROVERSION = '1.11'


class Client:
    """Use catalog discovery before any resource call, with no fallback."""

    def __init__(self, session, region_name=None, interface='public',
                 microversion=None):
        if microversion is not None and microversion not in MICROVERSIONS:
            raise ValueError('Select --os-coast-api-microversion '
                             'from 1.0 through 1.31.')
        self.stacks = stacks.StackManager(self)
        self.microversion = microversion
        self.adapter = adapter.Adapter(
            session=session, service_type='app-hosting',
            region_name=region_name, interface=interface,
            default_microversion=microversion)
        catalog_url = self.adapter.get_endpoint()
        if not catalog_url:
            raise exceptions.EndpointNotFound(
                'No app-hosting endpoint in the selected catalog.')
        data = discover.Discover(
            session, catalog_url, authenticated=False).data_for('1')
        if not data:
            raise exceptions.DiscoveryFailure('No app-hosting API major 1.')
        minimum = data['min_microversion']
        maximum = data['max_microversion']
        if minimum is None or maximum is None:
            raise exceptions.DiscoveryFailure(
                'app-hosting discovery must advertise microversion bounds.')
        if microversion is None:
            supported = [value for value in MICROVERSIONS
                         if minimum <= discover.normalize_version_number(
                             value) <= maximum]
            if not supported:
                raise exceptions.DiscoveryFailure(
                    'No common app-hosting microversion.')
            microversion = supported[-1]
        selected = discover.normalize_version_number(microversion)
        self.microversion = microversion
        self.adapter.default_microversion = microversion
        if not minimum <= selected <= maximum:
            raise exceptions.DiscoveryFailure(
                'Selected app-hosting microversion is outside '
                'discovery bounds.')
        self.max_microversion = maximum
        self.adapter.endpoint_override = data['url']

    def request(self, path, method, **kwargs):
        """Send a version-relative request and preserve HTTP outcomes."""
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Use a version-relative API path.')
        route = path.split('?', 1)[0]
        if route == '/v1' or route.startswith('/v1/'):
            raise ValueError('Discovery supplies /v1; do not repeat it.')
        # Headers and microversion= cannot change the selected version.
        # Legacy feature hints raise the per-call version when needed;
        # they never lower a newer negotiated version.
        tls_version = kwargs.pop('domain_tls_microversion', None)
        version = self.microversion
        if tls_version is not None:
            if tls_version != '1.2' or '/domains' not in route:
                raise ValueError(
                    'Domain TLS writes require microversion 1.2.')
            version = tls_version
        hook_version = kwargs.pop('webhooks_microversion', None)
        if hook_version is not None:
            if hook_version != '1.3' or '/webhooks' not in route:
                raise ValueError('Webhook requests require microversion 1.3.')
            version = hook_version
        health_version = kwargs.pop('health_check_microversion', None)
        if health_version is not None:
            parts = route.strip('/').split('/')
            if (health_version != '1.4' or not (
                    (parts == ['services'] and method.upper() == 'POST') or
                    (len(parts) == 2 and parts[0] == 'services' and
                     method.upper() == 'PATCH'))):
                raise ValueError(
                    'Health check writes require microversion 1.4.')
            version = health_version
        kind_version = kwargs.pop('kind_microversion', None)
        if kind_version is not None:
            if (kind_version != KIND_MICROVERSION or
                    route.strip('/') != 'services' or
                    method.upper() != 'POST'):
                raise ValueError('Workload kind requires microversion %s.'
                                 % KIND_MICROVERSION)
            version = kind_version
        # The console POST negotiates 1.9 for that call only. The
        # selected microversion is unchanged, as with health checks.
        console_version = kwargs.pop('console_microversion', None)
        if console_version is not None:
            parts = route.strip('/').split('/')
            if (console_version != CONSOLE_MICROVERSION or
                    method.upper() != 'POST' or len(parts) != 3 or
                    parts[0] not in ('apps', 'services') or
                    parts[2] != 'console' or not parts[1]):
                raise ValueError(
                    'Container terminal requests require microversion %s.'
                    % CONSOLE_MICROVERSION)
            version = console_version
        resize_version = kwargs.pop('server_resize_microversion', None)
        if resize_version is not None:
            parts = route.strip('/').split('/')
            if (resize_version != '1.26' or method.upper() != 'POST' or
                    len(parts) != 5 or parts[0] != 'stacks' or
                    parts[2] != 'servers' or parts[4] != 'action' or
                    not parts[1] or not parts[3]):
                raise ValueError('Server resize requires microversion 1.26.')
            version = resize_version
        flavor_version = kwargs.pop('flavors_microversion', None)
        if flavor_version is not None:
            if (flavor_version != '1.27' or route != '/flavors' or
                    method.upper() != 'GET'):
                raise ValueError('Flavor list requires microversion 1.27.')
            if self.max_microversion < (1, 27):
                raise exceptions.DiscoveryFailure(
                    'Flavor list needs Coast API 1.27 or later.')
            version = flavor_version
        if route == '/database_engines' or route.split('/')[1] == 'databases':
            if self.max_microversion < (1, 29):
                raise exceptions.DiscoveryFailure(
                    'Database commands require Coast API 1.29 or later.')
            version = '1.29'
        source_version = kwargs.pop('source_microversion', None)
        if source_version is not None:
            if source_version not in ('1.30', '1.31'):
                raise ValueError('Unsupported app source API version.')
            required = tuple(map(int, source_version.split('.')))
            if self.max_microversion < required:
                raise exceptions.DiscoveryFailure(
                    'App source needs API version %s or later.' %
                    source_version)
            version = max((version, source_version),
                          key=discover.normalize_version_number)
        version = max((version, self.microversion),
                      key=discover.normalize_version_number)
        headers = dict(kwargs.pop('headers', {}))
        for key in list(headers):
            if key.lower() == 'openstack-api-version':
                del headers[key]
        headers['OpenStack-API-Version'] = 'app-hosting ' + version
        kwargs['microversion'] = version
        # Env values can have arbitrary keys: do not log resource payloads.
        kwargs['log'] = False
        kwargs['connect_retries'] = 0
        kwargs['status_code_retries'] = 0
        return self.adapter.request(path, method, headers=headers, **kwargs)
