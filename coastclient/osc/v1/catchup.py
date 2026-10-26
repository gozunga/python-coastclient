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

"""Commands for the public Coast API through microversion 1.24."""
import argparse
import gzip
import os
from pathlib import Path
import tempfile

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import resource
from coastclient.osc.v1 import server
from coastclient.osc.v1 import utils


class StorageList(resource.ResourceCommand, command.Lister):
    """List persistent storage mounts in a stack."""

    admin_scope = 'list'

    route = '/stacks/{stack_id}/storages'
    list_filters = 'storage'
    method = 'GET'
    envelope = 'storages'
    paginated = True
    query = ('service_id',)
    column_priority = ()
    fields = ('id', 'type', 'name', 'source', 'container_path', 'size_gb',
              'read_only', 'status')


class StorageShow(resource.ResourceCommand, command.ShowOne):
    """Show a storage mount and its current status."""

    route = '/stacks/{stack_id}/storages/{storage_id}'
    method = 'GET'
    envelope = 'storage'


class StorageCreate(resource.ResourceCommand, command.ShowOne):
    """Save a volume or bind mount; the change applies on the next deploy."""

    route = '/stacks/{stack_id}/storages'
    method = 'POST'
    envelope = 'storage'
    options = (
        'name',
        'size_gb',
        'type',
        'service_id',
        'container_path',
        'host_path',
        'read_only')


class StorageSet(resource.ResourceCommand, command.ShowOne):
    """Save storage mount settings; the change applies on the next deploy."""

    route = '/stacks/{stack_id}/storages/{storage_id}'
    method = 'PATCH'
    envelope = 'storage'
    options = ('name', 'container_path', 'host_path', 'read_only')


class StorageDelete(resource.ResourceCommand, command.Command):
    """Delete a storage mount without waiting for completion."""

    route = '/stacks/{stack_id}/storages/{storage_id}'
    method = 'DELETE'
    envelope = 'storage'


class VolumeList(resource.ResourceCommand, command.Lister):
    """List data volumes in a stack."""

    admin_scope = 'list'

    route = '/stacks/{stack_id}/volumes'
    list_filters = 'volume'
    method = 'GET'
    envelope = 'volumes'
    paginated = True
    fields = ('id', 'name', 'size_gb', 'status', 'purpose', 'mount_path')


class VolumeShow(resource.ResourceCommand, command.ShowOne):
    """Show a data volume and its current status."""

    admin_scope = 'read'

    route = '/stacks/{stack_id}/volumes/{volume_id}'
    method = 'GET'
    envelope = 'volume'


class VolumeCreate(resource.ResourceCommand, command.ShowOne):
    """Create a data volume without waiting for provisioning."""

    route = '/stacks/{stack_id}/volumes'
    method = 'POST'
    envelope = 'volume'
    options = ('name', 'size_gb', 'server_id', 'volume_type', 'mount_path')
    required = ('name', 'size_gb')


class VolumeSet(resource.ResourceCommand, command.ShowOne):
    """Extend a data volume without waiting for completion."""

    route = '/stacks/{stack_id}/volumes/{volume_id}'
    method = 'PATCH'
    envelope = 'volume'
    options = ('size_gb',)


class VolumeDelete(resource.ResourceCommand, command.Command):
    """Delete a data volume without waiting for completion."""

    route = '/stacks/{stack_id}/volumes/{volume_id}'
    method = 'DELETE'
    envelope = 'volume'


class FileMountList(resource.ResourceCommand, command.Lister):
    """List file mounts for an app or service."""

    route = '/services/{service_id}/file_mounts'
    method = 'GET'
    envelope = 'file_mounts'
    fields = ('id', 'container_path', 'resource_name')


class FileMountShow(resource.ResourceCommand, command.ShowOne):
    """Show file mount metadata without revealing file content."""

    route = '/services/{service_id}/file_mounts/{file_mount_id}'
    method = 'GET'
    envelope = 'file_mount'


class FileMountCreate(resource.ResourceCommand, command.ShowOne):
    """Create a file mount from at most 64 KiB of UTF-8 content."""

    route = '/services/{service_id}/file_mounts'
    method = 'POST'
    envelope = 'file_mount'
    options = ('container_path', 'resource_name')
    required = ('container_path',)
    input_field = 'content'


class FileMountSet(resource.ResourceCommand, command.ShowOne):
    """Update file mount metadata or UTF-8 content (at most 64 KiB)."""

    route = '/services/{service_id}/file_mounts/{file_mount_id}'
    method = 'PATCH'
    envelope = 'file_mount'
    options = ('container_path', 'resource_name')
    input_field = 'content'
    file_required = False


class FileMountDelete(resource.ResourceCommand, command.Command):
    """Delete a file mount from an app or service."""

    route = '/services/{service_id}/file_mounts/{file_mount_id}'
    method = 'DELETE'
    envelope = 'file_mount'


class ExportList(resource.ResourceCommand, command.Lister):
    """List stack exports and their retention status."""

    admin_scope = 'list'

    route = '/stacks/{stack_id}/exports'
    list_filters = 'export'
    method = 'GET'
    envelope = 'exports'
    paginated = True
    fields = ('id', 'status', 'object_expires_at', 'artifacts')

    def prepare_rows(self, api, args, rows):
        if rows and utils.scope_params(args):
            # Exports omit ownership and retain a project-scoped parent read.
            stack = utils.resource_data(utils.request(
                api, '/stacks/' + args.stack_id, 'GET'), 'stack')
            return [dict(row, project_id=stack.get('project_id'))
                    for row in rows]
        return rows


class ExportShow(resource.ResourceCommand, command.ShowOne):
    """Show a stack export and its download artifacts."""

    route = '/stacks/{stack_id}/exports/{export_id}'
    method = 'GET'
    envelope = 'export'


class ExportCreate(resource.ResourceCommand, command.ShowOne):
    """Start a stack export without waiting for completion."""

    route = '/stacks/{stack_id}/exports'
    method = 'POST'
    envelope = 'export'
    options = ('volume_ids',)


class ExportDelete(resource.ResourceCommand, command.Command):
    """Delete a stack export without waiting for completion."""

    route = '/stacks/{stack_id}/exports/{export_id}'
    method = 'DELETE'
    envelope = 'export'


class StorageAttach(resource.ResourceCommand, command.ShowOne):
    """Attach storage to an app or service without waiting."""

    route = '/stacks/{stack_id}/storages/{storage_id}/attach'
    method = 'POST'
    envelope = 'storage'
    options = ('service_id', 'container_path')
    required = ('service_id', 'container_path')
    action = 'attach'


class StorageDetach(resource.ResourceCommand, command.ShowOne):
    """Detach storage from its app or service without waiting."""

    route = '/stacks/{stack_id}/storages/{storage_id}/detach'
    method = 'POST'
    envelope = 'storage'
    raw_body = True


class VolumeSnapshot(resource.ResourceCommand, command.ShowOne):
    """Start a data volume snapshot without waiting for completion."""

    route = '/stacks/{stack_id}/volumes/{volume_id}/action'
    method = 'POST'
    envelope = 'volume'
    action = 'snapshot'


class ExportRenew(resource.ResourceCommand, command.ShowOne):
    """Renew export download URLs without extending object retention."""

    route = '/stacks/{stack_id}/exports/{export_id}/action'
    method = 'POST'
    envelope = 'export'
    action = 'renew_url'


class ServerShow(resource.ResourceCommand, command.ShowOne):
    """Show a managed server and its current status."""

    admin_scope = 'read'

    route = '/stacks/{stack_id}/servers/{server_id}'
    method = 'GET'
    envelope = 'server'

    def take_action(self, args):
        fields, values = super().take_action(args)
        values = list(values)
        if 'flavor' in fields:
            index = fields.index('flavor')
            value = values[index]
            if hasattr(value, 'machine_readable'):
                value = value.machine_readable()
            values[index] = server.FlavorColumn(value)
        return fields, values


class ServerFailover(resource.ResourceCommand, command.ShowOne):
    """Request managed server failover without waiting (admin only)."""

    route = '/stacks/{stack_id}/servers/{server_id}/action'
    method = 'POST'
    envelope = 'server'
    action = 'failover'


class StackSet(resource.ResourceCommand, command.ShowOne):
    """Update stack metadata and DNS settings without waiting."""

    route = '/stacks/{stack_id}'
    method = 'PATCH'
    envelope = 'stack'
    options = (
        'name',
        'description',
        'default_domain',
        'dns_zone_id',
        'dns_trust_id')
    nullable = ('default_domain', 'dns_zone_id', 'dns_trust_id')


class AppgroupSet(resource.ResourceCommand, command.ShowOne):
    """Update an app group name or description."""

    route = '/appgroups/{appgroup_id}'
    method = 'PATCH'
    envelope = 'appgroup'
    options = ('name', 'description')


class EnvironmentSet(resource.ResourceCommand, command.ShowOne):
    """Update an environment name or description."""

    route = '/environments/{environment_id}'
    method = 'PATCH'
    envelope = 'environment'
    options = ('name', 'description')

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument('--appgroup', metavar='APPGROUP',
                            help='App group name or ID.')
        return parser

    def take_action(self, args):
        scope = argparse.Namespace(environment=args.environment_id,
                                   appgroup=args.appgroup)
        args.environment_id, _data = environment.scoped_environment(
            self.app.client_manager.coast, scope, load=False)
        return super().take_action(args)


class DnsZoneList(resource.ResourceCommand, command.Lister):
    """List DNS zones available to the project or a delegated trust."""

    route = '/dns/zones'
    method = 'GET'
    envelope = 'zones'
    fields = ('id', 'name', 'project_id')
    query = ('trust_id',)


class PlanList(resource.ResourceCommand, command.Lister):
    """List operator plans for app engine, worker and build servers."""

    route = '/plans'
    method = 'GET'
    envelope = 'plans'
    fields = ('name', 'kind', 'roles', 'description')
    formatters = {'roles': utils.RolesColumn}


class TemplateList(resource.ResourceCommand, command.Lister):
    """List one-click service templates available in a stack."""

    route = '/templates'
    method = 'GET'
    envelope = 'templates'
    fields = ('id', 'name', 'description')
    query = ('stack_id',)
    required = ('stack_id',)


class TemplateShow(resource.ResourceCommand, command.ShowOne):
    """Show a one-click service template available in a stack."""

    route = '/templates/{template_id}'
    method = 'GET'
    envelope = 'template'
    query = ('stack_id',)
    required = ('stack_id',)


class EventList(resource.ResourceCommand, command.Lister):
    """List project events with optional resource and outcome filters.

    Stack and resource ID filters include events of deleted resources.
    """

    admin_scope = 'list'

    route = '/events'
    list_filters = 'event'
    method = 'GET'
    envelope = 'events'
    fields = (
        'created_at',
        'resource_type',
        'resource_id',
        'action',
        'outcome',
        'message')
    query = ('resource_type', 'resource_id', 'stack_id', 'action',
             'request_id')

    def get_parser(self, prog_name):
        parser = utils.event_list_parser(
            super().get_parser(prog_name), sortable=True)
        parser.add_argument('--sort-key', default='created_at',
                            choices=('id', 'project_id', 'stack_id',
                                     'resource_type', 'resource_id', 'action',
                                     'outcome', 'message', 'request_id',
                                     'created_at', 'updated_at'),
                            help='API sort field (default: created_at).')
        parser.add_argument('--sort-dir', default='desc',
                            choices=('asc', 'desc'),
                            help='Sort direction (default: desc).')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        utils.resolve_references(api, args)
        filters = {key: getattr(args, key) for key in self.query + ('outcome',)
                   if getattr(args, key) is not None}
        rows = utils.list_data(api, self.route, self.envelope, args, filters,
                               total_limit=args.limit)
        fields = self.fields
        if args.long:
            fields += ('id', 'stack_id', 'request_id')
        result = fields, (utils.get_dict_properties(row, fields)
                          for row in rows)
        return utils.project_column(args, rows, result)


class QuotaList(resource.ResourceCommand, command.Lister):
    """List project quota overrides (admin only)."""

    route = '/quotas'
    method = 'GET'
    envelope = 'quotas'
    fields = ('project_id', 'environments', 'servers', 'baremetal_servers',
              'build_servers', 'apps', 'domains', 'volume_gb', 'storage_gb')


class QuotaDefaults(resource.ResourceCommand, command.ShowOne):
    """Show default project resource limits."""

    route = '/quotas/defaults'
    method = 'GET'
    envelope = 'quota'


class QuotaDelete(resource.ResourceCommand, command.Command):
    """Reset a project quota to the default limits (admin only)."""

    route = '/quotas/{project_id}'
    method = 'DELETE'
    envelope = 'quota'


class BackupList(resource.ResourceCommand, command.Lister):
    """List database backups for a service."""

    route = '/services/{service_id}/backups'
    method = 'GET'
    envelope = 'backups'
    fields = (
        'id',
        'kind',
        'status',
        'size_bytes',
        'started_at',
        'finished_at')


class BackupCreate(resource.ResourceCommand, command.ShowOne):
    """Start a database backup without waiting; a backup policy is required."""

    route = '/services/{service_id}/backups'
    method = 'POST'
    envelope = 'backup'
    raw_body = True


class BackupShow(resource.ResourceCommand, command.ShowOne):
    """Show a database backup and its current status."""

    route = '/backups/{backup_id}'
    method = 'GET'
    envelope = 'backup'


class BackupDelete(resource.ResourceCommand, command.Command):
    """Delete a database backup without waiting for completion."""

    route = '/backups/{backup_id}'
    method = 'DELETE'
    envelope = 'backup'


class BackupRestore(resource.ResourceCommand, command.ShowOne):
    """Restore a database backup without waiting for completion."""

    route = '/backups/{backup_id}/restore'
    method = 'POST'
    envelope = 'restore'
    options = ('mode', 'name', 'confirmation')
    raw_body = True


class BackupPolicyShow(resource.ResourceCommand, command.ShowOne):
    """Show the database backup schedule and retention policy."""

    route = '/services/{service_id}/backup_policy'
    method = 'GET'
    envelope = 'backup_policy'


class BackupPolicySet(resource.ResourceCommand, command.ShowOne):
    """Update the backup policy; omitted fields keep their saved values."""

    route = '/services/{service_id}/backup_policy'
    method = 'PUT'
    envelope = 'backup_policy'
    options = (
        'enabled',
        'engine',
        'container',
        'hour',
        'minute',
        'keep_daily',
        'keep_weekly')


class ComposeShow(resource.ResourceCommand, command.ShowOne):
    """Show the desired Compose configuration for an app or service."""

    route = '/services/{service_id}/compose'
    method = 'GET'
    envelope = 'compose'


class ComposeSet(resource.ResourceCommand, command.ShowOne):
    """Save UTF-8 Compose YAML; deploy separately to apply it."""

    route = '/services/{service_id}/compose'
    method = 'PUT'
    envelope = 'compose'
    input_field = 'yaml'
    suppress_content = True


class ContainerList(resource.ResourceCommand, command.Lister):
    """List containers belonging to an app or service."""

    route = '/services/{service_id}/containers'
    method = 'GET'
    envelope = 'containers'
    fields = ('id', 'name', 'image', 'state', 'health', 'restart_count')


class ContainerShow(resource.ResourceCommand, command.ShowOne):
    """Show the current state and health of a container."""

    route = '/services/{service_id}/containers/{container_id}'
    method = 'GET'
    envelope = 'container'


class ContainerLogs(resource.ResourceCommand, command.Lister):
    """Read container logs once without following new output."""

    route = '/services/{service_id}/containers/{container_id}/logs'
    method = 'GET'
    envelope = 'logs'
    query = ('tail', 'since')
    fields = ('timestamp', 'stream', 'line')

    def prepare_rows(self, api, args, rows):
        return [dict(row, timestamp=row.get('ts')) for row in rows['lines']]


class MetricsShow(resource.ResourceCommand, command.ShowOne):
    """Show app or service metrics for the selected time range."""

    route = '/services/{service_id}/metrics'
    method = 'GET'
    envelope = 'metrics'
    query = ('range',)


class BackupDownload(command.Command):
    """Download and validate a backup dump before publishing the file."""

    route = '/backups/{backup_id}/download'
    method = 'GET'

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument('backup_id', type=utils.resource_id,
                            metavar='BACKUP_ID',
                            help='Backup UUID to download.')
        parser.add_argument('--file', required=True, help='Destination file.')
        return parser

    def take_action(self, args):
        target = Path(args.file)
        if target.exists():
            raise exceptions.CommandError('Destination file already exists.')
        result = utils.response(
            self.app.client_manager.coast,
            self.route.format(backup_id=args.backup_id), 'GET', stream=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    dir=target.parent, delete=False) as output:
                temporary = output.name
                for chunk in result.iter_content(chunk_size=65536):
                    output.write(chunk)
            # GzipFile accepts an empty stream without a gzip member.
            with open(temporary, 'rb') as downloaded:
                if downloaded.read(2) != b'\x1f\x8b':
                    raise ValueError('Missing gzip member')
            with gzip.open(temporary, 'rb') as dump:
                while dump.read(65536):
                    pass
            # Linking publishes the validated file atomically and fails if
            # another writer created the destination during the transfer.
            os.link(temporary, target)
        except Exception:
            raise exceptions.CommandError('Backup download failed.') from None
        finally:
            result.close()
            if temporary is not None:
                os.unlink(temporary)
