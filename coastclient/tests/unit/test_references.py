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

"""M5 exact resource lookup and pre-release parser contracts."""
import argparse
import io
from types import SimpleNamespace
from unittest import mock

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import ingress
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.osc.v1 import utils


ID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'


class ReferencesTest(base.BaseTestCase):

    def setUp(self):
        super().setUp()
        self.api = mock.Mock()
        self.app = SimpleNamespace(client_manager=SimpleNamespace(
            coast=self.api), stdout=io.StringIO(), stderr=io.StringIO())

    def test_exact_names_ids_missing_and_ambiguous_for_each_noun(self):
        for noun in ('stack', 'server', 'appgroup', 'environment',
                     'app', 'service'):
            for count in (0, 1, 2):
                with self.subTest(noun=noun, matches=count):
                    rows = [{'id': ID, 'name': 'chosen'}] * count
                    rows += [{'id': OTHER, 'name': 'chosen-prefix'}]
                    with mock.patch.object(utils, 'collection_data',
                                           return_value=rows) as collect:
                        self.assertEqual(ID, utils.find_resource(
                            self.api, ID.replace('-', ''), noun))
                        collect.assert_not_called()
                        kwargs = {'stack_id': OTHER, 'appgroup_id': OTHER}
                        if count == 1:
                            self.assertEqual(ID, utils.find_resource(
                                self.api, 'chosen', noun, **kwargs))
                        else:
                            error = self.assertRaises(
                                exceptions.CommandError, utils.find_resource,
                                self.api, 'chosen', noun, **kwargs)
                            label = ('app or service' if noun in (
                                'app', 'service') else
                                noun.replace('appgroup', 'app group'))
                            expected = (
                                utils.not_found(label, 'chosen')
                                if count == 0 else
                                "More than one %s is named 'chosen'; "
                                "use the ID." % label)
                            self.assertEqual(expected, str(error))

    def test_workload_names_resolve_within_selected_stack(self):
        rows = [{'id': ID, 'name': 'database', 'stack_id': ID},
                {'id': OTHER, 'name': 'database', 'stack_id': OTHER}]

        def collect(api, collection, filters=None):
            if collection == 'stacks':
                return [{'id': ID, 'name': 'chosen'}]
            return [row for row in rows if not filters or
                    row['stack_id'] == filters['stack_id']]

        with mock.patch.object(utils, 'collection_data',
                               side_effect=collect) as lookup:
            for noun in ('app', 'service'):
                self.assertEqual(ID, utils.find_resource(
                    self.api, 'database', noun, stack_id=ID))
                lookup.assert_called_with(
                    self.api, 'services', {'stack_id': ID})
                self.assertRaisesRegex(
                    exceptions.CommandError, 'More than one app or service',
                    utils.find_resource, self.api, 'database', noun)
            args = argparse.Namespace(stack='chosen', service='database')
            utils.resolve_references(self.api, args)
            self.assertEqual(ID, args.stack)
            self.assertEqual(ID, args.service)
            for cls, argv in (
                    (catchup.StorageList, []),
                    (catchup.StorageCreate, []),
                    (catchup.StorageAttach,
                     [OTHER, '--container-path', '/data'])):
                cmd = cls(self.app, None)
                args = cmd.get_parser('test').parse_args(
                    argv + ['--stack', 'chosen', '--service', 'database'])
                with mock.patch.object(utils, 'request', return_value={
                        cls.envelope: {}}) as request, mock.patch.object(
                        utils, 'list_data', return_value=[]) as listing:
                    cmd.take_action(args)
                    self.assertEqual(ID, args.service_id)
                    if cls is catchup.StorageList:
                        self.assertEqual(
                            {'service_id': ID}, listing.call_args.args[4])
                    else:
                        body = request.call_args.kwargs['json']
                        self.assertEqual(ID, next(iter(body.values()))[
                            'service_id'])

    def test_scoped_list_routes(self):
        for noun, collection, path, parents in (
                ('server', 'servers', '/stacks/' + ID + '/servers',
                 {'stack_id': ID}),
                ('appgroup', 'appgroups', '/stacks/' + ID + '/appgroups',
                 {'stack_id': ID}),
                ('environment', 'environments',
                 '/appgroups/' + ID + '/environments', {'appgroup_id': ID})):
            with mock.patch.object(utils, 'collection_data', return_value=[
                    {'id': OTHER, 'name': 'chosen'}]) as collect:
                self.assertEqual(OTHER, utils.find_resource(
                    self.api, 'chosen', noun, **parents))
                collect.assert_called_once_with(
                    self.api, collection, path=path)

    def test_generated_commands_resolve_parent_and_own_names(self):
        cases = (
            (catchup.StackSet, ['chosen', '--name', 'renamed'],
             '/stacks/' + ID),
            (catchup.ServerShow, ['chosen', '--stack', OTHER],
             '/stacks/' + OTHER + '/servers/' + ID),
            (catchup.ServerFailover, ['chosen', '--stack', OTHER],
             '/stacks/' + OTHER + '/servers/' + ID + '/action'),
            (catchup.AppgroupSet, ['chosen', '--name', 'renamed'],
             '/appgroups/' + ID),
            (catchup.EnvironmentSet, ['chosen', '--name', 'renamed'],
             '/environments/' + ID),
            (catchup.StorageList, ['--stack', 'chosen'],
             '/stacks/' + ID + '/storages'),
            (catchup.BackupCreate, ['--service', 'chosen'],
             '/services/' + ID + '/backups'))
        for cls, argv, path in cases:
            cmd = cls(self.app, None)
            args = cmd.get_parser('test').parse_args(argv)
            with mock.patch.object(utils, 'collection_data', return_value=[
                    {'id': ID, 'name': 'chosen'}]), mock.patch.object(
                    utils, 'request', return_value={cls.envelope: {}}), \
                    mock.patch.object(utils, 'list_data', return_value=[]):
                cmd.take_action(args)
                if cls.paginated:
                    self.assertEqual(path, utils.list_data.call_args.args[1])
                else:
                    self.assertEqual(path, utils.request.call_args.args[1])

    def test_handwritten_commands_resolve_names(self):
        cases = (
            (stack.ShowStack, ['chosen'], '/stacks/' + ID),
            (stack.DeleteStack, ['chosen'], '/stacks/' + ID),
            (server.AddServer, ['--stack', 'chosen', '--kind', 'vm',
                                '--role', 'worker', '--plan', 'worker'],
             '/stacks/' + ID + '/servers'),
            (server.ListServer, ['--stack', 'chosen'],
             '/stacks/' + ID + '/servers'),
            (server.RemoveServer, ['chosen', '--stack', OTHER],
             '/stacks/' + OTHER + '/servers/' + ID),
            (server.ResizeServer, ['chosen', '--stack', OTHER,
                                   '--flavor', 'larger'],
             '/stacks/' + OTHER + '/servers/' + ID + '/action'),
            (appgroup.CreateAppgroup, ['group', '--stack', 'chosen'],
             '/stacks/' + ID + '/appgroups'),
            (appgroup.ListAppgroup, ['--stack', 'chosen'],
             '/stacks/' + ID + '/appgroups'),
            (environment.CreateEnvironment,
             ['staging', '--appgroup', 'chosen'],
             '/appgroups/' + ID + '/environments'),
            (environment.ListEnvironment, ['--appgroup', 'chosen'],
             '/appgroups/' + ID + '/environments'))
        for cls, argv, path in cases:
            cmd = cls(self.app, None)
            args = cmd.get_parser('test').parse_args(argv)
            body = {'stack': {}, 'server': {}, 'appgroup': {},
                    'environment': {}}
            with mock.patch.object(utils, 'collection_data', return_value=[
                    {'id': ID, 'name': 'chosen'}]), mock.patch.object(
                    utils, 'request', return_value=body) as request, \
                    mock.patch.object(utils, 'list_data', return_value=[]) \
                    as listing:
                cmd.take_action(args)
                call = (listing.call_args if listing.called
                        else request.call_args)
                self.assertEqual(path, call.args[1])

    def test_create_workloads_resolve_parent_names_and_server(self):
        for cls in (service.CreateApp, service.CreateService):
            cmd = cls(self.app, None)
            args = cmd.get_parser('test').parse_args([
                'web', '--appgroup', 'chosen', '--environment', 'chosen',
                '--server', 'chosen', '--image', 'nginx', '--tag', 'stable'])
            body = {'service': {}, 'appgroup': {'stack_id': OTHER}}
            with mock.patch.object(utils, 'collection_data', return_value=[
                    {'id': ID, 'name': 'chosen'}]) as collection, \
                    mock.patch.object(utils, 'request',
                                      return_value=body) as request:
                cmd.take_action(args)
                collection.assert_any_call(
                    self.api, 'servers',
                    path='/stacks/' + OTHER + '/servers')
                values = request.call_args.kwargs['json']['service']
                for field in ('appgroup_id', 'environment_id', 'server_id'):
                    self.assertEqual(ID, values[field])

    def test_event_list_resolves_stack_name_in_filters(self):
        cmd = catchup.EventList(self.app, None)
        args = cmd.get_parser('test').parse_args([
            '--stack', 'chosen', '--outcome', 'success'])
        with mock.patch.object(utils, 'collection_data', return_value=[
                {'id': ID, 'name': 'chosen'},
                {'id': OTHER, 'name': 'chosen-prefix'}]) as lookup, \
                mock.patch.object(utils, 'list_data', return_value=[]) as rows:
            cmd.take_action(args)
            lookup.assert_called_once_with(self.api, 'stacks')
            rows.assert_called_once_with(
                self.api, '/events', 'events', args,
                {'stack_id': ID, 'outcome': 'success'}, total_limit=50)

    def test_generated_option_names_preserve_api_fields(self):
        cases = (
            (catchup.StorageCreate, ['--stack', OTHER, '--service', 'chosen'],
             'json', {'storage': {'service_id': ID}}),
            (catchup.StorageAttach,
             [ID, '--stack', OTHER, '--service', 'chosen',
              '--container-path', '/data'],
             'json', {'attach': {'service_id': ID,
                                 'container_path': '/data'}}),
            (catchup.VolumeCreate, ['--stack', OTHER, '--server', 'chosen',
                                    '--name', 'data', '--size-gb', '1'],
             'json', {'volume': {'server_id': ID, 'name': 'data',
                                 'size_gb': 1}}),
            (catchup.TemplateList, ['--stack', 'chosen'],
             'params', {'stack_id': ID}),
            (catchup.TemplateShow, ['postgresql', '--stack', 'chosen'],
             'params', {'stack_id': ID}),
            (catchup.DnsZoneList, ['--dns-trust', OTHER],
             'params', {'trust_id': OTHER}))
        for cls, argv, key, expected in cases:
            cmd = cls(self.app, None)
            args = cmd.get_parser('test').parse_args(argv)
            with mock.patch.object(utils, 'collection_data', return_value=[
                    {'id': ID, 'name': 'chosen'}]), mock.patch.object(
                    utils, 'request', return_value={cls.envelope: {}}) as req:
                cmd.take_action(args)
                self.assertEqual(expected, req.call_args.kwargs[key])

    def test_removed_spellings_are_rejected(self):
        cases = (
            (catchup.StackSet, [ID, '--stack', ID]),
            (ingress.ListIngressRule, [ID]),
            (catchup.StackSet, [ID, '--dns-zone-id', ID]),
            (catchup.StackSet, [ID, '--dns-trust-id', ID]),
            (catchup.DnsZoneList, ['--trust-id', ID]),
            (catchup.TemplateList, ['--stack', ID, '--stack-id', ID]),
            (catchup.TemplateShow, ['postgresql', '--stack', ID,
                                    '--stack-id', ID]),
            (catchup.EventList, ['--stack-id', ID]),
            (catchup.StorageCreate, ['--stack', ID, '--service-id', ID]),
            (catchup.StorageAttach, [ID, '--stack', ID, '--service', ID,
                                     '--container-path', '/data',
                                     '--service-id', ID]),
            (catchup.VolumeCreate, ['--stack', ID, '--name', 'data',
                                    '--size-gb', '1', '--server-id', ID]),
            (catchup.ExportCreate, ['--stack', ID, '--volume-ids', ID]),
            (stack.UpgradeStack, [ID, '--image', ID, '--image-id', ID]),
            (stack.UpgradeStack, [ID, '--image', ID, '--stack', ID]),
            (stack.RollbackStackUpgrade, [ID, '--stack', ID]),
            (service.CreateApp, ['web', '--appgroup', ID, '--environment', ID,
                                 '--image', 'nginx', '--tag', 'stable',
                                 '--port', '80']))
        for cls, argv in cases:
            with self.subTest(command=cls.__name__, argv=argv):
                parser = cls(self.app, None).get_parser('test')
                self.assertRaises(SystemExit, parser.parse_args, argv)
        self.api.request.assert_not_called()

    def test_dns_references_require_ids_and_allow_explicit_clear(self):
        for cls, prefix, option, field in (
                (catchup.StackSet, [ID], '--dns-zone', 'dns_zone_id'),
                (catchup.StackSet, [ID], '--dns-trust', 'dns_trust_id'),
                (catchup.DnsZoneList, [], '--dns-trust', 'trust_id')):
            parser = cls(self.app, None).get_parser('test')
            with self.subTest(option=option, command=cls.__name__):
                self.assertRaises(SystemExit, parser.parse_args,
                                  prefix + [option, 'a-name'])
                args = parser.parse_args(prefix + [option, OTHER])
                self.assertEqual(OTHER, getattr(args, field))
                self.assertRaises(SystemExit, parser.parse_args,
                                  prefix + [option, 'null'])
        self.api.request.assert_not_called()

    def test_create_and_set_disable_abbreviation(self):
        for cls in (stack.CreateStack, server.AddServer,
                    appgroup.CreateAppgroup, environment.CreateEnvironment,
                    service.CreateApp, service.CreateService,
                    service.SetApp, service.SetService, catchup.StackSet,
                    catchup.AppgroupSet, catchup.EnvironmentSet):
            parser = cls(self.app, None).get_parser('test')
            self.assertFalse(parser.allow_abbrev)
