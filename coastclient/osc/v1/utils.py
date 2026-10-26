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

"""Shared argument, output, and HTTP handling for resource commands."""
import argparse
from contextvars import ContextVar
import json
import sys
from urllib import parse
import uuid

from keystoneauth1 import exceptions as ks_exc
from osc_lib.cli import format_columns
from osc_lib import exceptions
from osc_lib import utils
from oslo_utils import strutils

CONTEXT = ContextVar('coast_command', default=None)
GENERIC_404_TEXTS = frozenset(('', 'not found', 'resource not found'))


# Public list headings are explicit so acronyms and sentence case stay stable.
FIELD_LABELS = {
    'appgroup_id': 'App group ID', 'app_group_id': 'App group ID',
    'id': 'ID', 'name': 'Name', 'status': 'Status', 'size_gb': 'Size (GiB)',
    'read_only': 'Read-only', 'origin': 'Origin',
    'container_path': 'Container path',
    'purpose': 'Purpose', 'mount_path': 'Mount path',
    'resource_name': 'Resource name', 'object_expires_at': 'Object expires at',
    'artifacts': 'Artifacts', 'project_id': 'Project ID', 'kind': 'Kind',
    'roles': 'Roles', 'description': 'Description', 'flavor': 'Flavor',
    'created_at': 'Created at', 'resource_type': 'Resource type',
    'resource_id': 'Resource ID', 'action': 'Action', 'outcome': 'Outcome',
    'message': 'Message', 'environments': 'Environments', 'servers': 'Servers',
    'baremetal_servers': 'Baremetal servers', 'build_servers': 'Build servers',
    'apps': 'Apps', 'domains': 'Domains', 'volume_gb': 'Volume (GiB)',
    'storage_gb': 'Storage (GiB)', 'size_bytes': 'Size (bytes)',
    'started_at': 'Started at', 'finished_at': 'Finished at', 'image': 'Image',
    'state': 'State', 'health': 'Health', 'restart_count': 'Restart count',
    'created': 'Created at', 'time': 'Created at',
    'ram': 'RAM (MiB)', 'disk': 'Disk (GiB)',
    'data_volume_gb': 'Data volume (GiB)',
    'memory_limit_mb': 'Memory limit (MiB)',
    'duration_seconds': 'Duration (seconds)',
    'sg_ingress_id': 'Ingress security group ID',
    'sg_mgmt_id': 'Management security group ID',
    'runtime_log': 'Runtime log',
    'task_owner': 'Task owner', 'task_started_at': 'Task started at',
}


class ListColumn(format_columns.ListColumn):
    """Render arbitrary items in API order and retain their wire values."""

    def human_readable(self):
        return ', '.join(str(item) for item in self._value or [])

    def machine_readable(self):
        return self._value


class RolesColumn(ListColumn):
    """Use alphabetical roles in tables and structured output."""

    def __init__(self, value):
        super().__init__(sorted(value or [], key=str))


def label(field):
    """Use one spelling for a field in list and show output."""
    if field in FIELD_LABELS.values():
        return field
    acronyms = {'id': 'ID', 'gb': 'GB', 'mib': 'MiB', 'gib': 'GiB',
                'ip': 'IP', 'fqdn': 'FQDN', 'tls': 'TLS', 'ram': 'RAM',
                'vcpus': 'vCPUs', 'url': 'URL', 'cpu': 'CPU', 'dns': 'DNS',
                'ssh': 'SSH', 'http': 'HTTP', 'https': 'HTTPS', 'api': 'API',
                'uuid': 'UUID', 'cidr': 'CIDR', 'uri': 'URI', 'tcp': 'TCP',
                'udp': 'UDP', 'ipv4': 'IPv4', 'ipv6': 'IPv6'}
    words = field.lower().replace('_', ' ').split()
    key = field.lower().replace(' ', '_')
    return FIELD_LABELS.get(key, ' '.join(
        acronyms.get(word, word.capitalize() if index == 0 else word)
        for index, word in enumerate(words)))


class ResourceID(str):
    """Canonical UUID for routes, original spelling for error messages."""

    def __new__(cls, value):
        result = super().__new__(cls, str(uuid.UUID(value)))
        result.original = value
        return result


def resource_id(value):
    try:
        return ResourceID(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Use an explicit resource UUID.')


def domain_suffix(value):
    if value == 'null':
        raise argparse.ArgumentTypeError('Use a domain suffix.')
    return value


def nonnegative(value):
    try:
        number = int(value)
        if number >= 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use zero or a positive integer.')


def positive(value):
    try:
        number = int(value)
        if number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Use an integer greater than zero.')


def response(api, path, method, **kwargs):
    """Show Coast client faults; keep server and proxy faults generic."""
    result = api_call(api.request, path, method, **kwargs)
    if (method.upper() in ('POST', 'PUT', 'PATCH', 'DELETE') and
            result.headers.get('X-Coast-Redeploy-Required', '').lower() ==
            'true'):
        print('Changes saved; run openstack coast deploy SERVICE '
              'to apply them.',
              file=sys.stderr)
    return result


def api_call(function, *args, **kwargs):
    """Apply shared CLI error handling to manager calls."""
    try:
        result = function(*args, **kwargs)
    except ks_exc.HttpError as error:
        messages = {400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
                    404: 'Not Found', 406: 'Not Acceptable', 409: 'Conflict',
                    503: 'Service Unavailable'}
        status = error.http_status
        message = messages.get(status, 'Request failed')
        body = {}
        generic_404 = False
        if 400 <= status < 500 and error.response is not None:
            try:
                body = error.response.json()
            except ValueError:
                body = {}
            if (isinstance(body, dict) and
                    body.get('faultcode') == 'Client' and
                    isinstance(body.get('faultstring'), str)):
                detail = strutils.mask_password(body['faultstring'])
                generic_404 = (status == 404 and
                               detail.strip().lower().rstrip('.') in
                               GENERIC_404_TEXTS)
                message = ' '.join(detail.split()) or message
        context = CONTEXT.get()
        if (status == 404 and context is not None and
                (message == messages[404] or generic_404)):
            raise exceptions.CommandError(
                missing_resource(args, context)) from None
        if status == 409 and isinstance(body, dict):
            current = body.get('current_status', body.get('status'))
            if current is None:
                for value in body.values():
                    if isinstance(value, dict):
                        current = value.get('current_status',
                                            value.get('status'))
                    if isinstance(current, str):
                        break
            if isinstance(current, str):
                message += ' (current status: %s)' % strutils.mask_password(
                    ' '.join(current.split()))
        raise exceptions.CommandError('Coast API: %s (HTTP %s).' % (
            message, status)) from None
    except ks_exc.ClientException:
        raise exceptions.CommandError('Coast API connection failed.') from None
    return result


def not_found(noun, value):
    """Use the same message for name misses and HTTP 404 responses."""
    noun = ('app or service' if noun in ('app', 'service') else noun)
    noun = noun.replace('appgroup', 'app group').replace('_', ' ')
    return "%s '%s' not found." % (noun.capitalize(),
                                   getattr(value, 'original', value))


def missing_resource(args, context):
    """Identify the addressed resource from the failing request route.

    Collection and singleton child routes address their closest parent.
    Nested item routes may refer to a missing parent or child.
    Never issue another request to disambiguate a server response.
    """
    path = args[0] if args and isinstance(args[0], str) else ''
    resources = {'stacks': 'stack', 'services': 'service',
                 'databases': 'database',
                 'appgroups': 'appgroup', 'environments': 'environment',
                 'servers': 'server', 'domains': 'domain',
                 'deployments': 'deployment', 'containers': 'container',
                 'volumes': 'volume', 'storages': 'storage',
                 'file_mounts': 'file_mount', 'backups': 'backup',
                 'exports': 'export', 'ingress_rules': 'rule',
                 'quotas': 'project', 'templates': 'template'}
    parts = parse.urlsplit(path).path.strip('/').split('/')
    if len(parts) == 1:
        # Top-level collections take their parents as query filters or body
        # fields (template list --stack, app list --appgroup, app create).
        # A 404 there means a named parent is missing.
        parents = []
        for noun in ('stack', 'appgroup', 'environment', 'server'):
            value = context.args.get(noun + '_id') or context.args.get(noun)
            if value is not None:
                parents.append(not_found(noun, value)[:-len(' not found.')])
        if parents:
            rest = [item[0].lower() + item[1:] for item in parents[1:]]
            return ' or '.join(parents[:1] + rest) + ' not found.'
    # Routes alternate collection and identifier. Read them left to right
    # so an identifier that happens to equal a collection name ("services")
    # stays an identifier, and skip literal singleton children.
    singletons = {'quotas': ('defaults',)}
    matches = []
    index = 0
    while index < len(parts) - 1:
        part = parts[index]
        if part in resources:
            value = parts[index + 1]
            if value not in singletons.get(part, ()):
                matches.append((resources[part], value))
            index += 2
        else:
            index += 1
    if matches:
        descriptions = []
        for noun, value in matches:
            value = (context.args.get(noun) or
                     context.args.get(noun + '_id') or
                     (context.args.get('target')
                      if noun == 'service' else None) or value)
            descriptions.append(not_found(noun, value)[:-len(' not found.')])
        rest = [item[0].lower() + item[1:] for item in descriptions[1:]]
        return ' or '.join(descriptions[:1] + rest) + ' not found.'
    # Manager methods may hide their route; retain the command context.
    noun = context.noun
    key = {'app': 'service', 'quota': 'project'}.get(noun, noun)
    value = (context.args.get(key) or context.args.get(key + '_id') or
             context.args.get('target') or context.args.get('name'))
    return not_found(noun, value) if value else (
        context.target(missing=True) + '.')


def request(api, path, method, **kwargs):
    """Read the public JSON envelope from a Coast response."""
    result = response(api, path, method, **kwargs)
    return response_data(result)


def response_data(result):
    """Decode JSON with the shared invalid-response error."""
    if not result.content:
        return {}
    try:
        return result.json()
    except ValueError:
        raise exceptions.CommandError('Invalid Coast API response.') from None


def resource_data(data, envelope):
    """Require the resource envelope before formatting accepted output."""
    if not isinstance(data, dict) or not isinstance(data.get(envelope), dict):
        raise exceptions.CommandError('Invalid Coast API response.')
    return data[envelope]


def get_dict_properties(data, fields, formatters=None):
    """Format list values consistently in show and list commands."""
    list_formatters = {key: ListColumn for key, value in data.items()
                       if isinstance(value, list)}
    list_formatters.update(formatters or {})
    return utils.get_dict_properties(data, fields, formatters=list_formatters)


def show(data):
    columns = sorted(data)
    formatters = {key: format_columns.DictColumn
                  for key, value in data.items() if isinstance(value, dict)}
    return columns, get_dict_properties(data, columns, formatters)


def log_notices(app, log):
    """Explain unavailable or historical output without changing log rows."""
    unavailable = log.get('unavailable')
    note = log.get('note')
    reason = None
    if unavailable:
        reason = unavailable if isinstance(unavailable, str) else (
            log.get('reason') or note or 'Log output is unavailable.')
        app.stderr.write(str(reason) + '\n')
    if log.get('captured_at_failure'):
        app.stderr.write(
            'Stored runtime output captured at failure time '
            '(captured %s; deployment %s).\n' % (
                log.get('captured_at') or 'unknown',
                log.get('deployment_id') or 'unknown'))
    elif note and note != reason:
        app.stderr.write(str(note) + '\n')


def print_deleted(app, data, envelope):
    if envelope in data:
        app.stdout.write(json.dumps(data[envelope], sort_keys=True) + '\n')


def project_id(value):
    """Reject an empty explicit project scope during argument parsing."""
    if not value.strip():
        raise argparse.ArgumentTypeError('Project ID must not be empty.')
    return value


def scope_params(args):
    """Translate explicit administrator scope into API query parameters."""
    if getattr(args, 'all_projects', False):
        return {'all_projects': 'true'}
    if getattr(args, 'project', None):
        return {'project_id': args.project}
    return {}


def project_column(args, rows, result):
    """Include ownership whenever an administrator selects a scope."""
    columns, values = result
    if not scope_params(args):
        return columns, values
    return (tuple(columns) + ('Project ID',),
            (tuple(value) + (row.get('project_id'),)
             for row, value in zip(rows, values)))


def list_parser(parser, resource=None):
    if resource:
        from coastclient.osc.v1 import filters
        filters.add_options(parser, resource)
    parser.add_argument('--limit', type=positive, help='API page size.')
    parser.add_argument('--marker', type=resource_id, help='Pagination UUID.')
    parser.add_argument('--sort-key', help='API sort field.')
    parser.add_argument('--sort-dir', choices=('asc', 'desc'),
                        help='Sort direction: asc or desc.')
    return parser


def event_list_parser(parser, detail=False, sortable=False):
    order = 'selected sort order' if sortable else 'newest-first order'
    parser.add_argument('--limit', type=positive, default=50, metavar='N',
                        help='Show at most N events in total; '
                             'the default is 50. Follow pages as needed. '
                             'Use --marker with the last event ID to read '
                             'more history.')
    parser.add_argument('--marker', type=resource_id,
                        help='Continue after this event ID in %s.' % order)
    parser.add_argument('--outcome', choices=('start', 'success', 'failure'),
                        help='Filter events by outcome.')
    parser.add_argument('--long', action='store_true',
                        help=('Include ID, Stack ID, Request ID and '
                              'JSON Detail.'
                              if detail else
                              'Include ID, Stack ID and Request ID.'))
    return parser


def list_data(api, path, envelope, args, filters=None, single_page=False,
              total_limit=None, server_stack_id=None):
    """Follow collection links only within the discovered collection."""
    params = {key: getattr(args, key) for key in (
        'limit', 'marker', 'sort_key', 'sort_dir')
        if getattr(args, key) is not None}
    from coastclient.osc.v1 import filters as list_filters
    selected = list_filters.query(args)
    if 'stack_id' in selected:
        selected['stack_id'] = find_resource(
            api, selected['stack_id'], 'stack')
    if 'server_id' in selected:
        selected['server_id'] = find_resource(
            api, selected['server_id'], 'server',
            stack_id=(selected.get('stack_id') or server_stack_id or
                      getattr(args, 'stack_id', None) or
                      getattr(args, 'stack', None)))
    params.update(selected)
    params.update(filters or {})
    scope = scope_params(args)
    scope.update({key: params[key] for key in ('all_projects', 'project_id')
                  if key in params})
    if envelope in ('servers', 'volumes') and 'project_id' in scope:
        # Infrastructure parent reads require the explicit admin switch.
        scope['all_projects'] = 'true'
    params.update(scope)
    endpoint = api.adapter.endpoint_override.rstrip('/') + '/'
    collection = parse.urljoin(endpoint, path.lstrip('/'))
    seen = set()
    rows = []
    while True:
        page_url = collection
        if params:
            page_url += '?' + parse.urlencode(params)
        if page_url in seen:
            raise exceptions.CommandError('Invalid Coast pagination loop.')
        seen.add(page_url)
        body = request(api, path, 'GET', params=params)
        rows.extend(body[envelope])
        if total_limit is not None and len(rows) >= total_limit:
            return rows[:total_limit]
        link = next((item['href'] for item in body.get('links', [])
                     if item.get('rel') == 'next'), None)
        if single_page or not link:
            return rows
        target = parse.urlsplit(parse.urljoin(page_url, link))
        base = parse.urlsplit(collection)
        if (target.scheme, target.netloc, target.path) != (
                base.scheme, base.netloc, base.path) or target.fragment:
            raise exceptions.CommandError('Invalid Coast pagination link.')
        params = dict(parse.parse_qsl(target.query, keep_blank_values=True))
        params.update(scope)


def collection_data(api, resource, filters=None, path=None):
    """Read every page without command pagination options."""
    args = argparse.Namespace(limit=None, marker=None, sort_key=None,
                              sort_dir=None)
    return list_data(api, path or '/' + resource, resource, args, filters)


def is_resource_id(value):
    """Return whether a reference is a UUID rather than a name."""
    try:
        resource_id(value)
    except argparse.ArgumentTypeError:
        return False
    return True


def find_resource(api, value, resource, appgroup_id=None, stack_id=None,
                  kind=None, details=False):
    """Resolve a reference; details confirms UUIDs and returns the row."""
    context = CONTEXT.get()
    if (context and scope_params(argparse.Namespace(**context.args)) and
            not is_resource_id(value)):
        raise exceptions.CommandError(
            'An explicit resource UUID is required with --all-projects '
            'or --project.')
    if not value.strip():
        raise argparse.ArgumentTypeError(
            'Resource reference must not be empty.')
    try:
        identity = resource_id(value)
        if details:
            return resource_data(request(
                api, "/%ss/%s" % (resource, identity), "GET"), resource)
        return identity
    except argparse.ArgumentTypeError:
        pass
    if resource in ('stack', 'service', 'app', 'database'):
        collection = {'stack': 'stacks', 'database': 'databases'}.get(
            resource, 'services')
        filters = ({'stack_id': stack_id}
                   if resource in ('service', 'app') and stack_id else None)
        rows = (collection_data(api, collection, filters) if filters else
                collection_data(api, collection))
    elif resource == 'environment':
        groups = ([{'id': appgroup_id}] if appgroup_id else
                  resource_rows(api, 'appgroups', stack_id))
        rows = [row for group in groups for row in collection_data(
            api, 'environments',
            path='/appgroups/' + group['id'] + '/environments')]
    else:
        rows = resource_rows(api, resource + 's', stack_id)
    matches = [row for row in rows if row.get('name') == value
               and (kind is None or row.get('kind') == kind)]
    noun = kind or ('app or service' if resource in ('app', 'service')
                    else resource)
    if not matches:
        raise exceptions.CommandError(
            not_found(noun, value))
    if len(matches) > 1:
        noun = noun.replace('appgroup', 'app group').replace('_', ' ')
        raise exceptions.CommandError(
            "More than one %s is named '%s'; use the ID." % (noun, value))
    return matches[0] if details else matches[0]['id']


def resource_rows(api, collection, stack_id=None):
    """Collect stack children within the selected parent or project."""
    stacks = ([{'id': stack_id}] if stack_id else
              collection_data(api, 'stacks'))
    return [row for stack in stacks for row in collection_data(
        api, collection, path='/stacks/' + stack['id'] + '/' + collection)]


def resolve_references(api, args):
    """Resolve command references in parent order, preserving API keys."""
    def parent(name):
        return (getattr(args, name + '_id', None) or
                getattr(args, name, None))

    for noun in ('stack', 'appgroup', 'environment', 'server', 'service'):
        for key in (noun, noun + '_id'):
            value = getattr(args, key, None)
            if value is None:
                continue
            stack_id = parent('stack')
            if noun == 'server' and not stack_id and parent('appgroup'):
                try:
                    resource_id(value)
                except argparse.ArgumentTypeError:
                    group = find_resource(
                        api, parent('appgroup'), 'appgroup', details=True)
                    stack_id = group['stack_id']
            resolved = find_resource(
                api, value, noun, appgroup_id=parent('appgroup'),
                stack_id=stack_id)
            setattr(args, key, resolved)


def child_summary(rows):
    return ['%s (%s) %s' % (row['name'], row['status'], row['id'])
            for row in rows]


def workload_children(api, filters):
    rows = collection_data(api, 'services', filters)
    return {field: child_summary([row for row in rows
                                 if row.get('kind') == kind])
            for field, kind in (('apps', 'app'), ('services', 'service'))}
