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

"""Regression coverage for M5 Coast issue 108."""
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import env
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils
from coastclient.tests.unit import test_commands


class Audit108Test(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.api = SimpleNamespace(microversion='1.28')
        self.app = SimpleNamespace(
            stdout=io.StringIO(),
            client_manager=SimpleNamespace(coast=self.api))

    def execute(self, cls, argv):
        cmd = cls(self.app, None)
        return cmd.take_action(cmd.get_parser('test').parse_args(argv))

    def parse_dotenv(self, text, secret=None, build_time=None):
        args = SimpleNamespace(entries=[], from_file='-', secret=secret,
                               build_time=build_time)
        with mock.patch('sys.stdin', io.StringIO(text)):
            return env.read_values(args)

    def test_dashboard_quoting_and_comments(self):
        text = (r'export A = "one\n\t\r\"\\\q#literal" # secret' + '\n' +
                "B='literal\\n # build-time' # runtime-only\n" +
                'C=word#literal # build-time\nD= # ordinary comment\n')
        self.assertEqual({
            'A': {'value': 'one\n\t\r"\\\\q#literal', 'secret': True},
            'B': {'value': 'literal\\n # build-time', 'build_time': False},
            'C': {'value': 'word#literal', 'build_time': True},
            'D': {'value': ''}}, self.parse_dotenv(text))

    def test_line_errors_do_not_disclose_values(self):
        for text, expected in (
                ('A=private\nA=other', 'Line 2: A is defined twice'),
                ('A="private', 'Line 1: missing closing quote'),
                ("A='private' trailing", 'Line 1: unexpected text'),
                ('A=private # runtime-only, build-time', 'Line 1: A'),
                ('invalid line', 'line 1')):
            with self.subTest(text=text):
                error = self.assertRaises(exceptions.CommandError,
                                          self.parse_dotenv, text)
                self.assertIn(expected, str(error))
                self.assertNotIn('private', str(error))

    def test_marker_precedence_and_conflicts(self):
        self.assertEqual({
            'A': {'value': 'a', 'secret': True, 'build_time': False},
            'B': {'value': 'b', 'secret': True, 'build_time': False}},
            self.parse_dotenv('A=a # secret, runtime-only\nB=b', True, False))
        for text, secret, build in (
                ('A=a # secret', False, None),
                ('A=a # runtime-only', None, True),
                ('A=a # build-time', None, False)):
            error = self.assertRaises(exceptions.CommandError,
                                      self.parse_dotenv, text, secret, build)
            self.assertIn('Line 1: A', str(error))

    def test_mask_is_rejected_in_all_writes(self):
        for cls in (env.SetEnv, env.EnvReplace):
            with mock.patch('sys.stdin', io.StringIO(
                    'TOKEN=******** # secret')), mock.patch.object(
                        utils, 'request') as request:
                error = self.assertRaises(
                    exceptions.CommandError, self.execute, cls,
                    ['--service', test_commands.SERVER, '--from-file', '-'])
                self.assertIn('TOKEN at line 1', str(error))
                self.assertIn('keep the stored value', str(error))
                request.assert_not_called()

    def test_create_env_file_and_stdin(self):
        text = 'TOKEN=value # secret, runtime-only\nMODE=production\n'
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / '.env')
            Path(filename).write_text(text)
            for cls in (service.CreateApp, service.CreateService):
                for source in (filename, '-'):
                    for version in ('1.0', '1.28'):
                        self.api.microversion = version
                        with mock.patch('sys.stdin', io.StringIO(text)), \
                                mock.patch.object(
                                    utils, 'resolve_references'), \
                                mock.patch.object(
                                    utils, 'request', return_value={
                                        'service': {}}) as request:
                            self.execute(cls, [
                                'web', '--appgroup', test_commands.SERVER,
                                '--environment', test_commands.SERVER,
                                '--server', test_commands.SERVER,
                                '--image', 'example', '--tag', 'stable',
                                '--env-file', source])
                        self.assertEqual({
                            'TOKEN': {'value': 'value', 'secret': True,
                                      'build_time': False},
                            'MODE': {'value': 'production'}},
                            request.call_args.kwargs['json']['service']['env'])

    def test_create_env_rejects_mask_and_old_api_before_request(self):
        for cls in (service.CreateApp, service.CreateService):
            for version, text, expected in (
                    ('0.9', '', 'requires Coast API 1.0'),
                    ('1.28', 'A=******** # secret', 'keep the stored value')):
                self.api.microversion = version
                with mock.patch('sys.stdin', io.StringIO(text)), \
                        mock.patch.object(utils, 'request') as request:
                    error = self.assertRaises(
                        exceptions.CommandError, self.execute, cls, [
                            'web', '--appgroup', test_commands.SERVER,
                            '--environment', test_commands.SERVER,
                            '--server', test_commands.SERVER, '--image',
                            'example', '--tag', 'stable', '--env-file', '-'])
                    self.assertIn(expected, str(error))
                    request.assert_not_called()

    def test_container_logs_rows_and_formatters(self):
        for formatter in ('table', 'json', 'yaml'):
            with mock.patch.object(utils, 'resolve_references'), \
                    mock.patch.object(utils, 'request', return_value={
                        'logs': {'lines': [
                            {'ts': 'now', 'stream': 'stdout',
                             'line': 'ready'}]}}):
                columns, rows = self.execute(catchup.ContainerLogs, [
                    'container', '--service', test_commands.SERVER,
                    '-f', formatter])
            self.assertEqual(('Timestamp', 'Stream', 'Line'), columns)
            self.assertEqual([('now', 'stdout', 'ready')], list(rows))

    def test_documented_empty_replace_and_sentence_case(self):
        docs = Path('doc/source/commands.rst').read_text()
        self.assertIn('Set requires at least one key; replace also accepts',
                      docs)
        self.assertNotIn('App Group ID', docs)
        self.assertEqual('App group ID', utils.label('appgroup_id'))
