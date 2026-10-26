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

"""Regression coverage for the M5 CLI audit, Coast issue 102."""
import argparse
import io
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from keystoneauth1 import exceptions as ks_exc
from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import command
from coastclient.osc.v1 import env
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils
from coastclient.tests.unit import test_commands


class AuditFixesTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.api = mock.Mock()
        self.app = SimpleNamespace(
            stdout=io.StringIO(),
            client_manager=SimpleNamespace(coast=self.api))

    def execute(self, cls, argv):
        cmd = cls(self.app, None)
        return cmd.take_action(cmd.get_parser('test').parse_args(argv))

    def test_env_usage_and_flag_explanations(self):
        syntax = ('openstack coast env set --service SERVICE '
                  '[KEY=VALUE ...] [KEY] [--from-file FILE|-] '
                  '[--secret|--no-secret] [--build-time|--no-build-time]')
        parser = env.SetEnv(self.app, None).get_parser(
            'openstack coast env set')
        self.assertEqual('usage: ' + syntax, parser.format_usage().strip())
        self.assertIn(syntax, parser.format_help())
        reference = Path(__file__).resolve().parents[3] / (
            'doc/source/commands.rst')
        self.assertIn(syntax, reference.read_text())
        actions = {action.dest: action for action in parser._actions}
        self.assertIn('hidden prompts', actions['entries'].help)
        for action in parser._actions:
            if action.dest in ('service', 'from_file', 'secret', 'build_time'):
                self.assertTrue(action.help)
            if action.dest == 'build_time':
                self.assertIn('on for non-template apps and services at API '
                              '1.6+', action.help)
                self.assertIn('template services are runtime-only',
                              action.help)

    def test_env_writes_require_envelope(self):
        for cls, entries in ((env.SetEnv, ['KEY=value']),
                             (env.EnvReplace, ['KEY=value']),
                             (env.UnsetEnv, ['KEY'])):
            for data in ({}, None, [], {'env': []}):
                with self.subTest(command=cls.__name__, response=data), \
                        mock.patch.object(utils, 'find_resource',
                                          return_value=test_commands.SERVER), \
                        mock.patch.object(utils, 'request', return_value=data):
                    error = self.assertRaises(
                        exceptions.CommandError, self.execute, cls,
                        ['--service', test_commands.SERVER, *entries])
                    self.assertEqual('Invalid Coast API response.', str(error))
                    self.assertEqual('', self.app.stdout.getvalue())

    def test_environment_scope_checks_names_and_uuids(self):
        group = test_commands.APPGROUP
        identity = test_commands.ENVIRONMENT
        for cls in (environment.ShowEnvironment,
                    environment.DeleteEnvironment):
            for reference in ('production', identity):
                for owner in (group, test_commands.STACK):
                    row = {'id': identity, 'name': 'production',
                           'appgroup_id': owner}
                    with self.subTest(command=cls.__name__,
                                      reference=reference,
                                      owner=owner), \
                            mock.patch.object(utils, 'collection_data',
                                              return_value=[row]), \
                            mock.patch.object(utils, 'request',
                                              return_value={'environment': row}
                                              ) as request, \
                            mock.patch.object(utils, 'workload_children',
                                              return_value={}) as children:
                        argv = [reference, '--appgroup', group]
                        if owner == group:
                            self.execute(cls, argv)
                            if cls is environment.DeleteEnvironment:
                                self.assertEqual('DELETE',
                                                 request.call_args.args[2])
                        else:
                            error = self.assertRaises(
                                exceptions.CommandError, self.execute,
                                cls, argv)
                            self.assertIn('not found in app group', str(error))
                            self.assertTrue(str(error).endswith('.'))
                            self.assertEqual(1, request.call_count)
                            children.assert_not_called()

    def test_environment_name_missing_from_selected_group(self):
        for cls in (environment.ShowEnvironment,
                    environment.DeleteEnvironment):
            with mock.patch.object(utils, 'collection_data',
                                   return_value=[]), \
                    mock.patch.object(utils, 'request') as request:
                error = self.assertRaises(
                    exceptions.CommandError, self.execute, cls,
                    ['production', '--appgroup', test_commands.APPGROUP])
                self.assertEqual(
                    "Environment 'production' not found in app group '%s'." %
                    test_commands.APPGROUP, str(error))
                request.assert_not_called()

    def test_server_plan_required_and_guidance(self):
        parser = server.AddServer(self.app, None).get_parser('test')
        with mock.patch('sys.stderr', io.StringIO()):
            error = self.assertRaises(
                SystemExit, parser.parse_args,
                ['--stack', 'shop', '--kind', 'vm', '--role', 'worker'])
        self.assertEqual(2, error.code)
        self.api.request.assert_not_called()
        self.assertIn('openstack coast plan list', parser.format_help())

    def test_webhook_failures_only_optional_for_known_statuses(self):
        for cls in (service.ShowApp, service.ShowAppWebhook):
            argv = ([test_commands.SERVER] if cls is service.ShowApp else
                    ['--app', test_commands.SERVER])
            for status in (400, 403, 404, 406, 500, 503):
                self.api.request.side_effect = ks_exc.HttpError(
                    http_status=status)
                with self.subTest(command=cls.__name__, status=status), \
                        mock.patch.object(utils, 'request', return_value={
                            'service': {'id': test_commands.SERVER}}), \
                        mock.patch.object(service, 'worker_ip'):
                    if cls is service.ShowApp and status in (403, 404, 406):
                        self.execute(cls, argv)
                    else:
                        error = self.assertRaises(
                            exceptions.CommandError, self.execute, cls, argv)
                        self.assertNotIn('disabled', str(error))
            for failure in (ks_exc.ConnectFailure(),
                            RuntimeError('unexpected')):
                self.api.request.side_effect = failure
                expected = (exceptions.CommandError if isinstance(
                    failure, ks_exc.ClientException) else RuntimeError)
                self.assertRaises(expected, self.execute, cls, argv)
            self.api.request.side_effect = None
            self.api.request.return_value.json.return_value = {}
            self.api.request.return_value.content = b'{}'
            self.assertRaisesRegex(exceptions.CommandError,
                                   'Invalid Coast API response',
                                   self.execute, cls, argv)

    def test_nested_404_preserves_detail_or_names_ambiguity(self):
        args = argparse.Namespace(stack='shop', server='worker')
        context = command.Context(server.RemoveServer(self.app, None), args)
        token = utils.CONTEXT.set(context)
        self.addCleanup(utils.CONTEXT.reset, token)
        path = '/stacks/%s/servers/%s' % (
            test_commands.STACK, test_commands.SERVER)
        for body, expected in (
                ({'faultcode': 'Client', 'faultstring': 'Stack is missing.'},
                 'Coast API: Stack is missing. (HTTP 404).'),
                ({}, "Stack 'shop' or server 'worker' not found.")):
            response = mock.Mock()
            response.json.return_value = body
            self.api.request.side_effect = ks_exc.NotFound(response=response)
            error = self.assertRaises(exceptions.CommandError, utils.request,
                                      self.api, path, 'GET')
            self.assertEqual(expected, str(error))

    def test_schema_choices_reject_invalid_values_and_list_in_help(self):
        for cls, argv, option, choices in (
                (catchup.StorageCreate, ['--stack', 'shop'], '--type',
                 ('bind', 'volume')),
                (catchup.BackupRestore, [test_commands.SERVER], '--mode',
                 ('new_copy', 'overwrite')),
                (catchup.BackupPolicySet, ['--service', 'web'], '--engine',
                 ('postgres', 'mysql', 'mariadb', 'mongodb'))):
            parser = cls(self.app, None).get_parser('test')
            for value in choices:
                parsed = parser.parse_args([*argv, option, value])
                self.assertEqual(value, getattr(parsed, option[2:]))
            self.assertIn('{' + ','.join(choices) + '}', parser.format_help())
            with mock.patch('sys.stderr', io.StringIO()):
                error = self.assertRaises(SystemExit, parser.parse_args,
                                          [*argv, option, 'invalid'])
            self.assertEqual(2, error.code)
        self.api.request.assert_not_called()

    def test_client_messages_have_periods(self):
        parser = env.SetEnv(self.app, None).get_parser('test')
        args = parser.parse_args(['--service', 'web', 'KEY=a', 'KEY=b'])
        error = self.assertRaises(exceptions.CommandError,
                                  env.read_values, args)
        self.assertEqual('Duplicate env key.', str(error))
        self.api.request.side_effect = ks_exc.ConnectFailure()
        error = self.assertRaises(exceptions.CommandError, utils.request,
                                  self.api, '/stacks', 'GET')
        self.assertEqual('Coast API connection failed.', str(error))
        with mock.patch.object(utils, 'collection_data', return_value=[
                {'name': 'shop'}, {'name': 'shop'}]):
            error = self.assertRaises(exceptions.CommandError,
                                      utils.find_resource, self.api,
                                      'shop', 'stack')
        self.assertEqual("More than one stack is named 'shop'; use the ID.",
                         str(error))
        error = self.assertRaises(exceptions.CommandError, self.execute,
                                  catchup.StorageSet,
                                  ['--stack', test_commands.STACK,
                                   test_commands.SERVER])
        self.assertEqual('Nothing to change.', str(error))
