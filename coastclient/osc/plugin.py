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

"""OSC hooks for the coast namespace (catalog type app-hosting)."""
from osc_lib import utils

from coastclient.v1 import client

API_NAME = 'coast'
API_VERSION_OPTION = 'os_coast_api_version'
DEFAULT_API_VERSION = '1'
# Explicit microversions also select command major version 1.
API_VERSIONS = {version: 'coastclient.v1.client.Client'
                for version in ('1',) + client.MICROVERSIONS}


def make_client(instance):
    """Reuse OSC authentication, catalog filters, and transport settings."""
    version = instance._api_version[API_NAME]
    if version not in API_VERSIONS:
        raise ValueError(
            'Select --os-coast-api-version 1 or 1.0 through 1.31.')
    config = instance.get_configuration()
    microversion = config.get('coast_api_microversion')
    if version in client.MICROVERSIONS:
        microversion = version
    return client.Client(
        session=instance.session,
        region_name=instance.region_name,
        interface=instance.interface,
        microversion=microversion)


def build_option_parser(parser):
    """Keep command major version separate from REST microversion."""
    parser.add_argument(
        '--os-coast-api-version', choices=tuple(API_VERSIONS),
        default=utils.env('OS_COAST_API_VERSION', default=DEFAULT_API_VERSION),
        help='Command API major version 1 allows microversion negotiation; '
             '1.0 through 1.31 pins the REST microversion and '
             'overrides --os-coast-api-microversion / '
             'OS_COAST_API_MICROVERSION (Env: OS_COAST_API_VERSION).')
    parser.add_argument(
        '--os-coast-api-microversion', choices=client.MICROVERSIONS,
        default=utils.env('OS_COAST_API_MICROVERSION', default=None),
        help='REST microversion; default: newest common version '
             '(Env: OS_COAST_API_MICROVERSION).')
    return parser
