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

"""Wire contract tests for the API catch-up command set."""
import gzip
from importlib import metadata
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from osc_lib.command import command
from osc_lib import exceptions
from oslotest import base
import requests

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import domain
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils
from coastclient.tests.unit import labels


ID = '11111111-1111-4111-8111-111111111111'
STACK = ['--stack', ID]
SERVICE = ['--service', ID]
S = '/stacks/' + ID
W = '/services/' + ID
# Each case is command, argv, method, path, response envelope, request body.
CASES = [
    (catchup.StorageList,
     ['--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/storages',
     'storages',
     None),
    (catchup.StorageShow,
     [ID,
      '--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/storages/' + ID,
     'storage',
     None),
    (catchup.StorageDelete,
     [ID,
      '--stack',
      ID],
     'DELETE',
     '/stacks/' + ID + '/storages/' + ID,
     'storage',
     None),
    (catchup.StorageCreate,
     STACK + ['--type', 'volume', '--service', ID,
              '--container-path', '/data', '--name', 'data',
              '--size-gb', '10', '--read-only', 'off'],
     'POST', S + '/storages', 'storage',
     {'storage': {'type': 'volume', 'service_id': ID,
                  'container_path': '/data', 'name': 'data',
                  'size_gb': 10, 'read_only': False}}),
    (catchup.StorageSet,
     [ID,
      '--stack',
      ID,
      '--name',
      'renamed'],
     'PATCH',
     '/stacks/' + ID + '/storages/' + ID,
     'storage',
     {'storage': {'name': 'renamed'}}),
    (catchup.VolumeList,
     ['--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/volumes',
     'volumes',
     None),
    (catchup.VolumeShow,
     [ID,
      '--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/volumes/' + ID,
     'volume',
     None),
    (catchup.VolumeDelete,
     [ID,
      '--stack',
      ID],
     'DELETE',
     '/stacks/' + ID + '/volumes/' + ID,
     'volume',
     None),
    (catchup.VolumeCreate,
     ['--stack',
      ID,
      '--name',
      'data',
      '--size-gb',
      '10'],
     'POST',
     '/stacks/' + ID + '/volumes',
     'volume',
     {'volume': {'name': 'data',
                 'size_gb': 10}}),
    (catchup.VolumeSet,
     [ID,
      '--stack',
      ID,
      '--size-gb',
      '20'],
     'PATCH',
     '/stacks/' + ID + '/volumes/' + ID,
     'volume',
     {'volume': {'size_gb': 20}}),
    (catchup.FileMountList,
     ['--service',
      ID],
     'GET',
     '/services/' + ID + '/file_mounts',
     'file_mounts',
     None),
    (catchup.FileMountShow,
     [ID,
      '--service',
      ID],
     'GET',
     '/services/' + ID + '/file_mounts/' + ID,
     'file_mount',
     None),
    (catchup.FileMountDelete,
     [ID,
      '--service',
      ID],
     'DELETE',
     '/services/' + ID + '/file_mounts/' + ID,
     'file_mount',
     None),
    (catchup.FileMountCreate,
     ['--service',
      ID,
      '--container-path',
      '/etc/app.conf',
      '--file',
      '-'],
     'POST',
     '/services/' + ID + '/file_mounts',
     'file_mount',
     {'file_mount': {'container_path': '/etc/app.conf',
                     'content': 'content'}}),
    (catchup.FileMountSet,
     [ID,
      '--service',
      ID,
      '--file',
      '-'],
     'PATCH',
     '/services/' + ID + '/file_mounts/' + ID,
     'file_mount',
     {'file_mount': {'content': 'content'}}),
    (catchup.ExportList,
     ['--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/exports',
     'exports',
     None),
    (catchup.ExportShow,
     [ID,
      '--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/exports/' + ID,
     'export',
     None),
    (catchup.ExportDelete,
     [ID,
      '--stack',
      ID],
     'DELETE',
     '/stacks/' + ID + '/exports/' + ID,
     'export',
     None),
    (catchup.ExportCreate,
     ['--stack',
      ID,
      '--volume',
      ID],
     'POST',
     '/stacks/' + ID + '/exports',
     'export',
     {'export': {'volume_ids': [ID]}}),
    (catchup.StorageAttach,
     [ID,
      '--stack',
      ID,
      '--service',
      ID,
      '--container-path',
      '/data'],
     'POST',
     '/stacks/' + ID + '/storages/' + ID + '/attach',
     'storage',
     {'attach': {'service_id': ID,
                 'container_path': '/data'}}),
    (catchup.StorageDetach,
     [ID,
      '--stack',
      ID],
     'POST',
     '/stacks/' + ID + '/storages/' + ID + '/detach',
     'storage',
     {}),
    (catchup.VolumeSnapshot,
     [ID,
      '--stack',
      ID],
     'POST',
     '/stacks/' + ID + '/volumes/' + ID + '/action',
     'volume',
     {'snapshot': {}}),
    (catchup.ExportRenew,
     [ID,
      '--stack',
      ID],
     'POST',
     '/stacks/' + ID + '/exports/' + ID + '/action',
     'export',
     {'renew_url': {}}),
    (catchup.ServerShow,
     [ID,
      '--stack',
      ID],
     'GET',
     '/stacks/' + ID + '/servers/' + ID,
     'server',
     None),
    (catchup.ServerFailover,
     [ID,
      '--stack',
      ID],
     'POST',
     '/stacks/' + ID + '/servers/' + ID + '/action',
     'server',
     {'failover': {}}),
    (catchup.StackSet,
     [ID,
      '--name',
      'renamed',
      '--default-domain',
      'example.com',
      '--dns-zone',
      ID],
     'PATCH',
     '/stacks/' + ID,
     'stack',
     {'stack': {'name': 'renamed',
                'default_domain': 'example.com',
                'dns_zone_id': ID}}),
    (catchup.AppgroupSet,
     [ID,
      '--name',
      'renamed'],
     'PATCH',
     '/appgroups/' + ID,
     'appgroup',
     {'appgroup': {'name': 'renamed'}}),
    (catchup.EnvironmentSet,
     [ID,
      '--name',
      'renamed'],
     'PATCH',
     '/environments/' + ID,
     'environment',
     {'environment': {'name': 'renamed'}}),
    (catchup.DnsZoneList, [], 'GET', '/dns/zones', 'zones', None),
    (catchup.PlanList, [], 'GET', '/plans', 'plans', None),
    (catchup.TemplateList,
     ['--stack',
      ID],
     'GET',
     '/templates',
     'templates',
     None),
    (catchup.TemplateShow,
     ['postgresql',
      '--stack',
      ID],
     'GET',
     '/templates/postgresql',
     'template',
     None),
    (catchup.EventList, [], 'GET', '/events', 'events', None),
    (catchup.QuotaList, [], 'GET', '/quotas', 'quotas', None),
    (catchup.QuotaDefaults, [], 'GET', '/quotas/defaults', 'quota', None),
    (catchup.QuotaDelete,
     ['--project', ID],
     'DELETE',
     '/quotas/' + ID,
     'quota',
     None),
    (catchup.BackupList,
     ['--service',
      ID],
     'GET',
     '/services/' + ID + '/backups',
     'backups',
     None),
    (catchup.BackupCreate,
     ['--service',
      ID],
     'POST',
     '/services/' + ID + '/backups',
     'backup',
     {}),
    (catchup.BackupShow,
     [ID],
     'GET',
     '/backups/' + ID,
     'backup',
     None),
    (catchup.BackupDelete,
     [ID],
     'DELETE',
     '/backups/' + ID,
     'backup',
     None),
    (catchup.BackupRestore,
     [ID,
      '--mode',
      'overwrite',
      '--confirmation',
      'db'],
     'POST',
     '/backups/' + ID + '/restore',
     'restore',
     {'mode': 'overwrite',
      'confirmation': 'db'}),
    (catchup.BackupPolicyShow,
     ['--service',
      ID],
     'GET',
     '/services/' + ID + '/backup_policy',
     'backup_policy',
     None),
    (catchup.BackupPolicySet,
     ['--service',
      ID,
      '--enabled',
      'off',
      '--hour',
      '0'],
     'PUT',
     '/services/' + ID + '/backup_policy',
     'backup_policy',
     {'backup_policy': {'enabled': False,
                        'hour': 0}}),
    (catchup.ComposeShow,
     ['--service',
      ID],
     'GET',
     '/services/' + ID + '/compose',
     'compose',
     None),
    (catchup.ComposeSet,
     ['--service',
      ID,
      '--file',
      '-'],
     'PUT',
     '/services/' + ID + '/compose',
     'compose',
     {'compose': {'yaml': 'content'}}),
    (catchup.ContainerList,
     ['--service',
      ID],
     'GET',
     '/services/' + ID + '/containers',
     'containers',
     None),
    (catchup.ContainerShow,
     ['c-web',
      '--service',
      ID],
     'GET',
     '/services/' + ID + '/containers/c-web',
     'container',
     None),
    (catchup.ContainerLogs,
     ['c-web',
      '--service',
      ID,
      '--tail',
      '0'],
     'GET',
     '/services/' + ID + '/containers/c-web/logs',
     'logs',
     None),
    (catchup.MetricsShow,
     ['--service',
      ID,
      '--range',
      '7d'],
     'GET',
     '/services/' + ID + '/metrics',
     'metrics',
     None),
]


class CatchupTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.api = mock.Mock()
        self.api.adapter.endpoint_override = 'https://api.example.com/v1'
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO(), stderr=io.StringIO())

    def execute(self, cls, argv):
        cmd = cls(self.app, None)
        return cmd.take_action(cmd.get_parser('test').parse_args(argv))

    def respond(self, body, binary=None):
        result = requests.Response()
        result.status_code = 200
        result._content = (json.dumps(body).encode() if binary is None
                           else binary)
        self.api.request.return_value = result
        return result

    def test_every_new_command_is_registered_and_exercised(self):
        self.assertFalse(hasattr(catchup, 'StackUpgrade'))
        installed = {ep.load() for ep in metadata.entry_points(
            group='openstack.coast.v1')
            if ep.value.startswith('coastclient.osc.v1.catchup:')}
        self.assertEqual(installed,
                         {row[0] for row in CASES} |
                         {catchup.BackupDownload})

    def test_file_input_errors(self):
        for cls, argv in (
                (catchup.ComposeSet, SERVICE),
                (catchup.FileMountCreate,
                 SERVICE + ['--container-path', '/etc/app.conf'])):
            with TemporaryDirectory() as directory:
                target = Path(directory) / 'input.yaml'
                for content in (None, b'\xff'):
                    with self.subTest(command=cls, content=content):
                        if content is not None:
                            target.write_bytes(content)
                        error = self.assertRaises(
                            exceptions.CommandError, self.execute, cls,
                            argv + ['--file', str(target)])
                        self.assertEqual(
                            "Unable to read file '%s'." % target, str(error))
                        self.api.request.assert_not_called()

    def test_clear_health_port_help(self):
        for cls in (service.SetApp, service.SetService):
            parser = cls(self.app, None).get_parser('test')
            help_text = parser._option_string_actions[
                '--no-health-check-port'].help
            self.assertIn('override', help_text)
            self.assertIn('first app port is probed', help_text)
            self.assertNotIn('null', help_text)

    def test_download_validates_and_publishes_gzip(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'dump.gz'
            payload = gzip.compress(b'database dump')
            result = self.respond({}, payload)
            result.iter_content = lambda chunk_size: [payload]
            result.close = mock.Mock()
            self.execute(catchup.BackupDownload, [ID, '--file', str(target)])
            self.assertEqual(payload, target.read_bytes())
            self.api.request.assert_called_once_with(
                '/backups/' + ID + '/download', 'GET', stream=True)
            result.close.assert_called_once_with()

    def test_download_rejects_truncated_dump_without_partial_file(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'dump.gz'
            payload = gzip.compress(b'database dump')[:-4]
            result = self.respond({}, payload)
            result.iter_content = lambda chunk_size: [payload]
            result.close = mock.Mock()
            self.assertRaises(exceptions.CommandError, self.execute,
                              catchup.BackupDownload,
                              [ID, '--file', str(target)])
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_download_rejects_empty_transfer_without_publishing_file(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'dump.gz'
            result = self.respond({}, b'')
            result.iter_content = lambda chunk_size: []
            result.close = mock.Mock()
            self.assertRaises(exceptions.CommandError, self.execute,
                              catchup.BackupDownload,
                              [ID, '--file', str(target)])
            self.assertEqual([], list(Path(directory).iterdir()))
            result.close.assert_called_once_with()

    def test_download_preserves_destination_created_during_transfer(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'dump.gz'
            payload = gzip.compress(b'database dump')
            result = self.respond({}, payload)

            def transfer(chunk_size):
                target.write_bytes(b'existing file')
                yield payload

            result.iter_content = transfer
            result.close = mock.Mock()
            self.assertRaises(exceptions.CommandError, self.execute,
                              catchup.BackupDownload,
                              [ID, '--file', str(target)])
            self.assertEqual(b'existing file', target.read_bytes())
            self.assertEqual([target], list(Path(directory).iterdir()))
            result.close.assert_called_once_with()

    def test_export_create_splits_only_its_volume_ids_option(self):
        other = '22222222-2222-4222-8222-222222222222'
        self.respond({'export': {}})
        self.execute(catchup.ExportCreate,
                     STACK + ['--volume', ID, '--volume', other])
        self.api.request.assert_called_once_with(
            S + '/exports', 'POST',
            json={'export': {'volume_ids': [ID, other]}})

    def test_typed_storage_create_payloads_and_read_only(self):
        for mount_type, options, expected in (
                ('volume', ['--name', 'data', '--size-gb', '10'],
                 {'name': 'data', 'size_gb': 10}),
                ('bind', ['--host-path', '/srv/data'],
                 {'host_path': '/srv/data'})):
            for flag, enabled in (('on', True), ('off', False), (None, None)):
                with self.subTest(mount_type=mount_type, read_only=flag):
                    self.api.reset_mock()
                    values = dict(expected, type=mount_type, service_id=ID,
                                  container_path='/data')
                    argv = STACK + ['--type', mount_type, '--service', ID,
                                    '--container-path', '/data'] + options
                    if flag is not None:
                        argv += ['--read-only', flag]
                        values['read_only'] = enabled
                    self.respond({'storage': dict(values, id=ID)})
                    self.execute(catchup.StorageCreate, argv)
                    self.api.request.assert_called_once_with(
                        S + '/storages', 'POST', json={'storage': values})

    def test_list_labels_use_sentence_case_and_acronyms(self):
        for cls, argv, envelope, expected in (
                (catchup.StorageList, STACK, 'storages',
                 ('ID', 'Type', 'Name', 'Source', 'Container path',
                  'Size (GiB)', 'Read-only', 'Status')),
                (catchup.VolumeList, STACK, 'volumes',
                 ('ID', 'Name', 'Status', 'Size (GiB)', 'Purpose',
                  'Mount path')),
                (catchup.FileMountList, SERVICE, 'file_mounts',
                 ('ID', 'Container path', 'Resource name')),
                (catchup.ExportList, STACK, 'exports',
                 ('ID', 'Status', 'Object expires at', 'Artifacts')),
                (catchup.DnsZoneList, [], 'zones',
                 ('ID', 'Name', 'Project ID')),
                (catchup.EventList, [], 'events',
                 ('Created at', 'Resource type', 'Resource ID',
                  'Action', 'Outcome', 'Message')),
                (catchup.QuotaList, [], 'quotas',
                 ('Project ID', 'Environments', 'Servers', 'Baremetal servers',
                  'Build servers', 'Apps', 'Domains', 'Volume (GiB)',
                  'Storage (GiB)')),
                (catchup.BackupList, SERVICE, 'backups',
                 ('ID', 'Status', 'Kind', 'Size (bytes)', 'Started at',
                  'Finished at')),
                (catchup.ContainerList, SERVICE, 'containers',
                 ('ID', 'Name', 'Image', 'State', 'Health', 'Restart count')),
                (catchup.PlanList, [], 'plans',
                 ('Name', 'Kind', 'Roles', 'Description')),
                (catchup.TemplateList, ['--stack', ID], 'templates',
                 ('ID', 'Name', 'Description'))):
            with self.subTest(command=cls.__name__):
                self.respond({envelope: []})
                columns, _ = self.execute(cls, argv)
                self.assertEqual(expected, columns)

    def test_storage_list_typed_and_legacy_rows_match_show(self):
        mounts = [
            {'id': ID, 'type': 'volume', 'name': 'data', 'source': 'data',
             'container_path': '/data', 'size_gb': 10, 'read_only': False,
             'status': 'ACTIVE'},
            {'id': None, 'type': 'bind', 'name': None, 'source': '/srv/data',
             'container_path': '/data', 'size_gb': None, 'read_only': True,
             'status': 'ACTIVE'},
            {'id': ID, 'name': 'legacy', 'status': 'ACTIVE'}]
        self.respond({'storages': mounts})
        columns, rows = self.execute(catchup.StorageList, STACK)
        rows = list(rows)
        self.assertEqual(('ID', 'Type', 'Name', 'Source', 'Container path',
                          'Size (GiB)', 'Read-only', 'Status'), columns)
        for mount, row in zip(mounts, rows):
            self.respond({'storage': mount})
            fields, values = self.execute(catchup.StorageShow, [ID] + STACK)
            shown = dict(zip(fields, values))
            for column, value in zip(columns, row):
                self.assertEqual(shown.get(column, ''), value)
        self.assertIs(False, rows[0][columns.index('Read-only')])
        self.assertIs(True, rows[1][columns.index('Read-only')])

    def test_storage_service_filter_resolution_and_pagination(self):
        for reference in (ID, 'database'):
            with self.subTest(reference=reference):
                self.api.reset_mock()
                pages = []
                if reference != ID:
                    pages.append({'services': [{'id': ID, 'name': reference}]})
                pages.extend([
                    {'storages': [{'id': ID}], 'links': [
                        {'rel': 'next', 'href': '?service_id=' + ID +
                         '&marker=' + ID}]},
                    {'storages': [{'id': 'next'}]}])
                responses = [self.respond(page) for page in pages]
                self.api.request.side_effect = responses
                columns, rows = self.execute(
                    catchup.StorageList, STACK + ['--service', reference])
                self.assertEqual([ID, 'next'], [row[0] for row in rows])
                calls = self.api.request.call_args_list
                if reference != ID:
                    self.assertEqual(('/services', 'GET'), calls[0].args)
                self.assertEqual((S + '/storages', 'GET'), calls[-2].args)
                self.assertEqual({'params': {'service_id': ID}},
                                 calls[-2].kwargs)
                self.assertEqual({'params': {'service_id': ID, 'marker': ID}},
                                 calls[-1].kwargs)
                self.api.request.side_effect = None

    def test_dns_zone_help_explains_omitted_trust(self):
        parser = catchup.DnsZoneList(self.app, None).get_parser(
            'dns zone list')
        help_text = ' '.join(parser.format_help().split())
        self.assertIn('--dns-trust DNS_TRUST_ID', help_text)
        self.assertIn('Delegated DNS trust UUID; omit to list the caller '
                      'project.', help_text)

    def test_metrics_ranges_and_api_default(self):
        for selection in (None, '15m', '1h', '6h', '24h', '7d', '30d'):
            self.api.reset_mock()
            self.respond({'metrics': {}})
            argv = SERVICE + (['--range', selection] if selection else [])
            self.execute(catchup.MetricsShow, argv)
            self.assertEqual(
                {'params': {'range': selection}} if selection else {},
                self.api.request.call_args.kwargs)
        self.api.reset_mock()
        self.assertRaises(SystemExit, self.execute, catchup.MetricsShow,
                          SERVICE + ['--range', '2h'])
        self.api.request.assert_not_called()
        help_text = catchup.MetricsShow(self.app, None).get_parser(
            'metrics show').format_help()
        self.assertIn('API default of 1h', help_text)

    def test_stack_dns_link_can_be_cleared(self):
        self.respond({'stack': {}})
        self.execute(catchup.StackSet, [
            ID, '--no-default-domain', '--no-dns-zone'])
        self.api.request.assert_called_once_with(
            S, 'PATCH', json={'stack': {
                'default_domain': None, 'dns_zone_id': None}})

    def test_show_preserves_primary_and_task_fields(self):
        row = {'primary_url': 'https://www.example.com',
               'default_domain_enabled': False,
               'task_owner': 'Domain check',
               'task_started_at': '2026-10-19T00:00:00Z'}
        for cls in (service.ShowApp, service.ShowService):
            self.respond({'service': row})
            with mock.patch.object(service, 'webhook_urls', return_value={}):
                columns, values = self.execute(cls, [ID])
            shown = dict(zip(columns, values))
            for key, value in row.items():
                self.assertEqual(value, shown[utils.label(key)])
        self.respond({'server': {'id': ID, 'hostname': 'worker.example.com'}})
        columns, values = self.execute(catchup.ServerShow, [ID] + STACK)
        self.assertEqual('worker.example.com',
                         dict(zip(columns, values))[utils.label('hostname')])

    def test_domain_routing_and_primary(self):
        for cls, argv, method, path, expected in (
                (domain.AddDomain, SERVICE + ['--host', 'www', '--zone',
                 'zone', '--auto-dns', '--primary'], 'POST', W + '/domains',
                 {'host': 'www', 'zone_id': 'zone', 'auto_dns': True,
                  'primary': True}),
                (domain.SetDomain, [ID] + SERVICE + ['--primary',
                 '--zone', 'zone', '--host', '', '--no-auto-dns'],
                 'PATCH', W + '/domains/' + ID,
                 {'primary': True, 'zone_id': 'zone', 'host': '',
                  'auto_dns': False})):
            self.respond({'domain': expected})
            self.execute(cls, argv)
            self.api.request.assert_called_with(
                path, method, json={'domain': expected})

    def test_image_and_default_domain_updates(self):
        for cls in (service.SetApp, service.SetService):
            for state, enabled in (('--enable-default-domain', True),
                                   ('--disable-default-domain', False)):
                self.respond({'service': {}})
                self.execute(cls, [ID, state,
                                   '--image', 'nginx', '--tag', 'stable'])
                self.api.request.assert_called_with(
                    W, 'PATCH', json={'service': {
                        'default_domain_enabled': enabled,
                        'source': {'image': 'nginx', 'tag': 'stable'}}})


def wire_case(cls, argv, method, path, envelope, body):
    def test(self):
        is_list = issubclass(cls, command.Lister)
        returned = {'id': ID, 'name': 'resource', 'status': 'PENDING'}
        if cls is catchup.ContainerLogs:
            returned = {'ts': 'now', 'stream': 'stdout', 'line': 'ready'}
            self.respond({envelope: {'lines': [returned]}})
        else:
            self.respond({envelope: [returned] if is_list else returned})
        content = 'content'
        with mock.patch('sys.stdin', io.StringIO(content)):
            output = self.execute(cls, argv)
        call = self.api.request.call_args
        self.assertEqual((path, method), call.args)
        self.assertEqual(body, call.kwargs.get('json'))
        if cls in (catchup.TemplateList, catchup.TemplateShow):
            self.assertEqual({'stack_id': ID}, call.kwargs['params'])
        if cls is catchup.ContainerLogs:
            self.assertEqual({'tail': 0}, call.kwargs['params'])
        if cls is catchup.MetricsShow:
            self.assertEqual({'range': '7d'}, call.kwargs['params'])
        self.assertEqual(1, self.api.request.call_count)
        if is_list:
            columns, rows = output
            self.assertTrue(columns)
            self.assertEqual(1, len(list(rows)))
        elif issubclass(cls, command.ShowOne):
            self.assertEqual(labels.labelled(returned, cls),
                             dict(zip(*output)))
        else:
            self.assertIsNone(output)
            self.assertEqual('', self.app.stdout.getvalue())
    return test


for case in CASES:
    setattr(CatchupTest, 'test_wire_' + case[0].__name__, wire_case(*case))
