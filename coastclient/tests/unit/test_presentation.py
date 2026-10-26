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

"""Shared M5 heading, resource error, and process exit contracts."""
import argparse
import io
import json
from unittest import mock

import fixtures
from osc_lib import exceptions
from oslotest import base
import requests
import yaml

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import command
from coastclient.osc.v1 import domain
from coastclient.osc.v1 import env
from coastclient.osc.v1 import quota
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.osc.v1 import terminal
from coastclient.osc.v1 import utils
from coastclient.tests.unit import test_commands
from coastclient.tests.unit import test_phase6
from coastclient.v1 import client


class PresentationTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.set_up_api()

    set_up_api = test_commands.CommandTest.set_up_api
    execute = test_commands.CommandTest.execute
    register = test_commands.CommandTest.register
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)
    run_shell = test_phase6.Phase6Test.run_shell

    def test_shared_headings_for_list_and_show(self):
        row = {'id': test_commands.STACK, 'name': 'web', 'status': 'ACTIVE',
               'plan': 'standard',
               'engine_version': 'version', 'created_at': 'timestamp'}
        self.register('GET', 'stacks', json={'stacks': [row]})
        self.register('GET', test_commands.STACK_PATH, json={'stack': row})
        headings, rows = self.execute(stack.ListStack, [])
        fields, values = self.execute(stack.ShowStack, [test_commands.STACK])
        self.assertEqual(('ID', 'Name', 'Status', 'Plan', 'Engine version',
                          'Created at'), headings)
        shown = dict(zip(fields, values))
        self.assertEqual(dict(zip(headings, next(rows))), shown)
        self.assertEqual('Resource type', utils.label('resource_type'))
        self.assertEqual('Source ref', utils.label('Source Ref'))
        self.assertEqual('Health check', utils.label('health_check'))
        self.assertEqual('RAM (MiB)', utils.label('ram'))
        self.assertEqual('Size (GiB)', utils.label('size_gb'))
        self.assertEqual('Floating IP', utils.label('floating_ip'))
        self.assertEqual('App group ID', utils.label('appgroup_id'))
        self.assertEqual('DNS records', utils.label('dns_records'))
        self.assertEqual('SSH key ID', utils.label('ssh_key_id'))
        for field, heading in utils.FIELD_LABELS.items():
            self.assertEqual(heading, utils.label(field))
            self.assertEqual(heading, utils.label(heading))

    def test_admin_stack_security_group_headings(self):
        self.http.get(test_commands.ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.28', 'links': [
                {'rel': 'self', 'href': test_commands.BASE}]}]})
        self.app.client_manager.coast = client.Client(
            self.session, region_name='region', microversion='1.18')
        row = {'sg_ingress_id': test_commands.SERVER,
               'sg_mgmt_id': test_commands.APPGROUP}
        self.register('GET', test_commands.STACK_PATH, json={'stack': row})
        fields, values = self.execute(
            stack.ShowStack, [test_commands.STACK, '--all-projects'])
        self.assertEqual({
            'Ingress security group ID': test_commands.SERVER,
            'Management security group ID': test_commands.APPGROUP},
            dict(zip(fields, values)))

    def test_empty_project_validation_sentence(self):
        for value in ('', '   '):
            error = self.assertRaises(
                argparse.ArgumentTypeError, utils.project_id, value)
            self.assertEqual('Project ID must not be empty.', str(error))

    def test_size_headings_and_column_selection(self):
        cases = (
            (catchup.VolumeShow, [test_commands.SERVER, '--stack',
                                  test_commands.STACK],
             test_commands.STACK_PATH + '/volumes/' + test_commands.SERVER,
             'volume', {'size_gb': 0}, {'Size (GiB)': 0}),
            (catchup.StorageShow, [test_commands.SERVER, '--stack',
                                   test_commands.STACK],
             test_commands.STACK_PATH + '/storages/' + test_commands.SERVER,
             'storage', {'size_gb': None}, {'Size (GiB)': None}),
            (catchup.BackupShow, [test_commands.SERVER],
             'backups/' + test_commands.SERVER, 'backup',
             {'size_bytes': 123, 'duration_seconds': 4},
             {'Size (bytes)': 123, 'Duration (seconds)': 4}),
            (catchup.QuotaDefaults, [], 'quotas/defaults', 'quota',
             {'volume_gb': -1, 'storage_gb': None},
             {'Volume (GiB)': -1, 'Storage (GiB)': None}),
            (service.ShowService, [test_commands.SERVER],
             'services/' + test_commands.SERVER, 'service',
             {'memory_limit_mb': 512}, {'Memory limit (MiB)': 512}),
            (stack.ShowStack, [test_commands.STACK],
             test_commands.STACK_PATH, 'stack', {'data_volume_gb': 10},
             {'Data volume (GiB)': 10}),
            (catchup.ExportShow, [test_commands.SERVER, '--stack',
                                  test_commands.STACK],
             test_commands.STACK_PATH + '/exports/' + test_commands.SERVER,
             'export', {'artifacts': [{'size_bytes': 123}]},
             {'Artifacts': [{'size_bytes': 123}]}),
        )
        for cls, argv, path, envelope, data, expected in cases:
            self.register('GET', path, json={envelope: data})
            for formatter in ('table', 'json', 'yaml'):
                with self.subTest(command=cls.__name__, formatter=formatter):
                    self.app.stdout = io.StringIO()
                    columns = [arg for key in expected for arg in ('-c', key)]
                    self.execute(cls, [*argv, '-f', formatter, *columns], True)
                    output = self.app.stdout.getvalue()
                    if formatter == 'table':
                        for heading in expected:
                            self.assertIn(heading, output)
                    else:
                        result = (json.loads(output) if formatter == 'json'
                                  else yaml.safe_load(output))
                        self.assertEqual(expected, result)

    def test_404_names_addressed_resource_or_collection_parent(self):
        cases = (
            (stack.ShowStack, [test_commands.STACK],
             test_commands.STACK_PATH, 'stack', test_commands.STACK),
            (catchup.ServerShow,
             [test_commands.SERVER, '--stack', test_commands.STACK],
             test_commands.SERVERS_PATH + '/' + test_commands.SERVER,
             'server', test_commands.SERVER),
            (catchup.StorageShow,
             ['disk', '--stack', test_commands.STACK],
             test_commands.STACK_PATH + '/storages/disk', 'storage', 'disk'),
            (appgroup.ListAppgroup, ['--stack', test_commands.STACK],
             test_commands.STACK_PATH + '/appgroups',
             'stack', test_commands.STACK),
        )
        for cls, argv, path, noun, value in cases:
            with self.subTest(command=cls.__name__):
                self.register('GET', path, status_code=404, json={})
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls, argv)
                expected = utils.not_found(noun, value)
                if noun in ('server', 'storage'):
                    expected = ("Stack '%s' or %s" %
                                (test_commands.STACK,
                                 expected[0].lower() + expected[1:]))
                self.assertEqual(expected, str(error))
                self.assertIsNone(command.CONTEXT.get())

    def test_uuid_original_spelling_and_named_parent_context(self):
        value = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        args = argparse.Namespace(server=utils.resource_id(value), stack='web')
        context = command.Context(server.RemoveServer(self.app, None), args)
        self.assertEqual("Server %s in stack 'web'" % value, context.target())

    def test_context_keeps_original_arguments_and_multiword_resources(self):
        cases = (
            (terminal.ExecApp, argparse.Namespace(target='web'), "App 'web'"),
            (stack.CreateStack, argparse.Namespace(name='web'), "Stack 'web'"),
            (catchup.FileMountShow,
             argparse.Namespace(file_mount_id='config', service_id='web'),
             "File mount 'config' in service 'web'"),
            (stack.RollbackStackUpgrade, argparse.Namespace(stack='web'),
             "Stack 'web'"),
        )
        for cls, args, expected in cases:
            with self.subTest(command=cls.__name__):
                context = command.Context(cls(self.app, None), args)
                for key in vars(args):
                    setattr(args, key, 'resolved-id')
                self.assertEqual(expected, context.target())

    def test_409_preserves_message_and_returned_status_without_get(self):
        for extra, suffix in (
                ({}, ''),
                ({'status': 'DELETING'}, ' (current status: DELETING)'),
                ({'stack': {'status': 'UPGRADING'}},
                 ' (current status: UPGRADING)'),
                ({'current_status': 'ERROR'}, ' (current status: ERROR)')):
            route = self.register('DELETE', test_commands.STACK_PATH,
                                  status_code=409, json=dict(
                                      faultcode='Client',
                                      faultstring='Stack is busy.', **extra))
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      stack.DeleteStack, [test_commands.STACK])
            self.assertEqual(
                'Coast API: Stack is busy.%s (HTTP 409).' % suffix, str(error))
            self.assertEqual(1, route.call_count)

    def test_shell_api_exit_one_and_argparse_exit_two(self):
        self.useFixture(fixtures.FakeLogger(name='run'))
        for status in (400, 401, 403, 404, 406, 409, 500, 503):
            self.register('GET', test_commands.STACK_PATH, status_code=status,
                          json={'faultcode': 'Client',
                                'faultstring': 'Request rejected.'})
            result, app = self.run_shell(
                ['coast', 'stack', 'show', test_commands.STACK])
            self.assertEqual(1, result)
            self.assertEqual('', app.stdout.getvalue())
        self.register('GET', test_commands.STACK_PATH,
                      exc=requests.exceptions.ConnectionError)
        result, _ = self.run_shell(
            ['coast', 'stack', 'show', test_commands.STACK])
        self.assertEqual(1, result)
        before = len(self.http.request_history)
        error = self.assertRaises(
            SystemExit, self.run_shell,
            ['coast', 'stack', 'show'])
        self.assertEqual(2, error.code)
        self.assertTrue(all(request.url != test_commands.BASE +
                            test_commands.STACK_PATH for request in
                            self.http.request_history[before:]))

    def test_create_set_reject_abbreviated_options(self):
        for cls, argv in (
                (stack.CreateStack, ['web', '--pla', 'small']),
                (catchup.StackSet, [test_commands.STACK, '--des', 'text'])):
            cmd = cls(self.app, None)
            parser = cmd.get_parser('test')
            self.assertFalse(parser.allow_abbrev)
            error = self.assertRaises(SystemExit, parser.parse_args, argv)
            self.assertEqual(2, error.code)

    def test_named_stack_remains_named_after_resolution(self):
        self.register('GET', 'stacks', json={'stacks': [
            {'id': test_commands.STACK, 'name': 'web'}]})
        self.register('POST', test_commands.STACK_PATH + '/action',
                      status_code=404, json={})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, stack.UpgradeStack,
            ['web', '--image', test_commands.SERVER])
        self.assertEqual("Stack 'web' not found.", str(error))

    def test_named_scoped_404_survives_parent_and_target_resolution(self):
        self.register('GET', 'stacks', json={'stacks': [
            {'id': test_commands.STACK, 'name': 'web'}]})
        self.register('GET', test_commands.SERVERS_PATH, json={'servers': [
            {'id': test_commands.SERVER, 'name': 'worker'}]})
        self.register('GET', test_commands.SERVERS_PATH + '/' +
                      test_commands.SERVER, status_code=404, json={})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, catchup.ServerShow,
            ['worker', '--stack', 'web'])
        self.assertEqual(
            "Stack 'web' or server 'worker' not found.", str(error))

    def test_quota_rows_keep_limits_zeroes_and_absent_counters(self):
        data = {'quota': {'apps': 0, 'servers': -1, 'storage_gb': None,
                          'volume_gb': 200},
                'usage': {'in_use': {'apps': 0, 'servers': 2},
                          'reserved': {'apps': 0}}}
        self.register('GET', 'quotas/' + test_phase6.PROJECT, json=data)
        columns, rows = self.execute(quota.ShowQuota,
                                     ['--project', test_phase6.PROJECT])
        self.assertEqual(('Resource', 'Limit', 'In use', 'Reserved'), columns)
        self.assertEqual([('Apps', 0, 0, 0), ('Servers', -1, 2, None),
                          ('Storage (GiB)', None, None, None),
                          ('Volume (GiB)', 200, None, None)], list(rows))

    def test_service_child_404_names_parent(self):
        for cls, path in ((env.ListEnv, '/env'),
                          (catchup.ComposeShow, '/compose')):
            with self.subTest(command=cls.__name__):
                self.register('GET', 'services', json={'services': [
                    {'id': test_commands.SERVER, 'name': 'web'}]})
                self.register('GET',
                              'services/' + test_commands.SERVER + path,
                              status_code=404, json={})
                error = self.assertRaises(exceptions.CommandError,
                                          self.execute, cls,
                                          ['--service', 'web'])
                self.assertEqual("App or service 'web' not found.", str(error))

    def test_empty_references_are_usage_errors_before_client_access(self):
        for cls, argv in (
                (stack.ShowStack, ['']),
                (appgroup.ListAppgroup, ['--stack', '']),
                (env.ListEnv, ['--service', '  ']),
                (catchup.ServerShow, ['', '--stack', test_commands.STACK]),
                (service.ShowApp, ['']),
                (terminal.ExecApp, ['']),
                (catchup.VolumeShow, ['', '--stack', test_commands.STACK]),
                (catchup.FileMountShow,
                 ['', '--service', test_commands.SERVER]),
                (catchup.BackupShow, [''])):
            with self.subTest(command=cls.__name__):
                app = mock.Mock(spec=['stdout', 'stderr'])
                parser = cls(app, None).get_parser('test')
                error = self.assertRaises(SystemExit, parser.parse_args, argv)
                self.assertEqual(2, error.code)
        error = self.assertRaises(SystemExit, self.run_shell,
                                  ['coast', 'appgroup', 'list', '--stack', ''])
        self.assertEqual(2, error.code)
        self.assertFalse([request for request in self.http.request_history
                          if '/v1/stacks/' in request.url])

    def test_domain_name_and_id_misses_share_wording(self):
        base = 'services/' + test_commands.SERVER + '/domains'
        self.register('GET', base, json={'domains': []})
        self.register('GET', base + '/' + test_commands.STACK,
                      status_code=404, json={})
        for reference in ('missing.example.com', test_commands.STACK):
            error = self.assertRaises(
                exceptions.CommandError, self.execute, domain.ShowDomain,
                [reference, '--service', test_commands.SERVER])
            expected = (utils.not_found('domain', reference)
                        if reference == 'missing.example.com' else
                        "App or service '%s' or domain '%s' not found." %
                        (test_commands.SERVER, reference))
            self.assertEqual(expected, str(error))

    def test_template_list_404_names_query_scoped_stack(self):
        self.register('GET', 'stacks', json={'stacks': [
            {'id': test_commands.STACK, 'name': 'web'}]})
        route = self.register('GET', 'templates', status_code=404, json={})
        for reference in (test_commands.STACK,
                          test_commands.STACK.replace('-', ''), 'web'):
            with self.subTest(reference=reference):
                before = len(self.http.request_history)
                error = self.assertRaises(
                    exceptions.CommandError, self.execute,
                    catchup.TemplateList, ['--stack', reference])
                self.assertEqual("Stack '%s' not found." % reference,
                                 str(error))
                self.assertEqual({'stack_id': [test_commands.STACK]},
                                 route.last_request.qs)
                self.assertEqual(2 if reference == 'web' else 1,
                                 len(self.http.request_history) - before)

    def test_app_list_404_names_query_scoped_parents(self):
        self.register('GET', 'services', status_code=404, json={})
        for parent in ('appgroup', 'environment'):
            self.register('GET', parent + 's/' + test_commands.STACK,
                          json={parent: {'id': test_commands.STACK}})
        for option, noun in (('--appgroup', 'App group'),
                             ('--environment', 'Environment')):
            with self.subTest(option=option):
                error = self.assertRaises(
                    exceptions.CommandError, self.execute,
                    service.ListApp, [option, test_commands.STACK])
                self.assertEqual("%s '%s' not found." % (
                    noun, test_commands.STACK), str(error))

    def test_quota_defaults_404_does_not_name_a_project(self):
        self.register('GET', 'quotas/defaults', status_code=404, json={})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, catchup.QuotaDefaults, [])
        self.assertNotIn("'defaults'", str(error))
        self.assertIn('not found', str(error))

    def test_identifier_equal_to_collection_name_is_kept(self):
        path = 'services/%s/containers/services' % test_commands.SERVER
        self.register('GET', path, status_code=404, json={})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, catchup.ContainerShow,
            ['services', '--service', test_commands.SERVER])
        self.assertEqual(
            "App or service '%s' or container 'services' not found." %
            test_commands.SERVER, str(error))

    def test_template_show_404_still_names_template(self):
        self.register('GET', 'templates/missing', status_code=404, json={})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, catchup.TemplateShow,
            ['missing', '--stack', test_commands.STACK])
        self.assertEqual("Template 'missing' not found.", str(error))
