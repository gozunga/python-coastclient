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

"""Consistent exact-match options for public API list filters."""

import argparse
import math

from coastclient.osc.v1 import utils


FIELDS = {
    'stack': (
        'id', 'name', 'status', 'plan', 'description', 'default_domain',
        'dns_zone_id', 'engine_version', 'fault',
    ),
    'server': (
        'id', 'name', 'status', 'kind', 'role', 'hostname', 'floating_ip',
    ),
    'appgroup': (
        'id', 'name', 'status', 'description',
    ),
    'environment': (
        'id', 'name', 'status', 'description',
    ),
    'service': (
        'id', 'name', 'status', 'stack_id', 'server_id', 'template_id',
        'description', 'ports', 'replicas', 'memory_limit_mb', 'auto_deploy',
        'cpu_limit', 'health_check', 'health_check_port', 'healthcheck_path',
        'current_deployment_id',
    ),
    'deployment': (
        'id', 'status', 'number', 'source_ref', 'server_id', 'trigger',
        'rollback_of', 'fault',
    ),
    'domain': (
        'id', 'status', 'fqdn', 'tls', 'auto_dns', 'dns_mode',
    ),
    'volume': (
        'id', 'name', 'status', 'server_id', 'volume_type', 'mount_path',
        'purpose', 'size_gb',
    ),
    'storage': (
        'id', 'name', 'status', 'server_id', 'type', 'container_path',
        'host_path', 'source', 'origin', 'read_only', 'size_gb',
    ),
    'export': (
        'id', 'status',
    ),
    'event': (
        'id', 'message',
    ),
}

STATUSES = {
    'stack': (
        'ACTIVE', 'BUILDING', 'DEGRADED', 'DELETED', 'DELETING', 'ERROR',
        'PENDING', 'ROLLING_BACK', 'UPGRADING',
    ),
    'server': (
        'ACTIVE', 'BUILDING', 'DELETING', 'ERROR', 'FAILOVER', 'PENDING',
        'UNHEALTHY',
    ),
    'appgroup': (
        'ACTIVE', 'DELETED', 'DELETING', 'ERROR', 'PENDING',
    ),
    'environment': (
        'ACTIVE', 'DELETED', 'DELETING', 'ERROR', 'PENDING',
    ),
    'service': (
        'ACTIVE', 'DEGRADED', 'DELETING', 'DEPLOYING', 'ERROR', 'PENDING',
        'STARTING', 'STOPPED',
    ),
    'deployment': (
        'ACTIVE', 'BUILDING', 'CANCELLED', 'DEPLOYING', 'FAILED', 'PENDING',
        'ROLLED_BACK', 'SUPERSEDED',
    ),
    'domain': (
        'ACTIVE', 'ERROR', 'PENDING_DNS', 'PENDING_VERIFICATION', 'VERIFIED',
    ),
    'volume': (
        'AVAILABLE', 'CREATING', 'DELETING', 'ERROR', 'IN_USE',
        'SNAPSHOTTING',
    ),
    'storage': (
        'AVAILABLE', 'IN_USE',
    ),
    'export': (
        'ACTIVE', 'DELETING', 'ERROR', 'EXPIRED', 'PENDING', 'SNAPSHOTTING',
        'UPLOADING',
    ),
}

CHOICES = {
    'health_check': ('none', 'http'),
    'kind': ('vm', 'baremetal'),
    'role': ('control', 'worker', 'build'),
    'type': ('volume', 'bind'),
    'purpose': ('data', 'docker'),
    'tls': ('letsencrypt', 'none'),
    'dns_mode': ('designate', 'manual'),
    'trigger': ('api', 'rollback', 'webhook', 'reconcile'),
}


def finite_cpu(value):
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use a finite CPU limit in cores.')


def health_port(value):
    try:
        number = int(value)
        if 1 <= number <= 65535:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use a port from 1 to 65535.')


def add_options(parser, resource):
    """Declare exact-match filters without sending omitted options."""
    fields = FIELDS[resource]
    parser.set_defaults(list_filter_fields=fields)
    for field in fields:
        option = field.removesuffix('_id') if field in (
            'stack_id', 'server_id') else field
        option = ('health-check-path' if field == 'healthcheck_path'
                  else option.replace('_', '-'))
        label = {
            'memory_limit_mb': 'memory limit in MiB',
            'size_gb': 'size in GiB',
            'health_check': 'HTTP probe mode',
            'health_check_port': 'HTTP probe port',
            'healthcheck_path': 'HTTP probe path',
            'auto_dns': 'automatic DNS',
        }.get(field, utils.label(field))
        if label[0].isupper() and not label.split()[0].isupper():
            label = label[0].lower() + label[1:]
        help_text = 'Filter by exact %s.' % label
        if field in ('auto_deploy', 'auto_dns', 'read_only'):
            group = parser.add_mutually_exclusive_group()
            for prefix, value in (('', 'true'), ('no-', 'false')):
                group.add_argument(
                    '--' + prefix + option, dest=field,
                    action='store_const', const=value, default=None,
                    help='Filter by %s %s.' % (label, value))
            continue
        choices = STATUSES[resource] if field == 'status' else (
            CHOICES.get(field))
        if field.endswith('_id'):
            help_text += (' Accepts a name or ID.' if field in (
                'stack_id', 'server_id') else ' Use an ID.')
        kwargs = {'type': int} if field in (
            'number', 'replicas', 'memory_limit_mb', 'size_gb') else {}
        if field == 'cpu_limit':
            kwargs['type'] = finite_cpu
            help_text += ' Use a finite number of CPU cores.'
        elif field == 'health_check_port':
            kwargs['type'] = health_port
            help_text += ' Use an integer from 1 to 65535.'
        if choices:
            kwargs['choices'] = choices
            help_text += ' Choices: %s.' % ', '.join(choices)
        parser.add_argument('--' + option, dest=field,
                            metavar=option.upper().replace('-', '_'),
                            help=help_text, **kwargs)
    return parser


def query(args):
    return {field: getattr(args, field) for field in getattr(
        args, 'list_filter_fields', ()) if getattr(args, field) is not None}
