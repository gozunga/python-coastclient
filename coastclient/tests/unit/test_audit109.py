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

"""Regression coverage for M5 Coast issue 109 log diagnostics."""
import io
import json
from types import SimpleNamespace
from unittest import mock

from oslotest import base
import yaml

from coastclient.osc.v1 import deployment
from coastclient.osc.v1 import logs
from coastclient.osc.v1 import utils
from coastclient.tests.unit import test_commands


class Audit109Test(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.app = SimpleNamespace(
            stdout=io.StringIO(), stderr=io.StringIO(),
            client_manager=SimpleNamespace(
                coast=SimpleNamespace(microversion='1.28')))

    def execute(self, cls, data, formatter, columns=()):
        self.app.stdout = io.StringIO()
        self.app.stderr = io.StringIO()
        cmd = cls(self.app, None)
        argv = ['--service', test_commands.SERVER, '-f', formatter]
        if cls is not logs.ShowLogs:
            argv.insert(0, test_commands.STACK)
        for column in columns:
            argv.extend(['-c', column])
        args = cmd.get_parser('test').parse_args(argv)
        with mock.patch.object(utils, 'find_resource', return_value='id'), \
                mock.patch.object(utils, 'request',
                                  return_value=data) as request:
            cmd.run(args)
            request.assert_called_once()
        output = self.app.stdout.getvalue()
        if formatter == 'json':
            return json.loads(output)
        if formatter == 'yaml':
            return yaml.safe_load(output)
        return output

    def test_log_commands_reject_follow(self):
        for cls in (logs.ShowLogs, deployment.LogDeployment):
            cmd = cls(self.app, None)
            argv = ['--service', test_commands.SERVER, '--follow']
            if cls is deployment.LogDeployment:
                argv.insert(0, test_commands.STACK)
            with mock.patch('sys.stderr', io.StringIO()), \
                    mock.patch.object(utils, 'request') as request:
                error = self.assertRaises(
                    SystemExit, cmd.get_parser('test').parse_args, argv)
            self.assertEqual(2, error.code)
            request.assert_not_called()

    def test_incomplete_deployment_log_returns_after_one_read(self):
        for formatter in ('table', 'json', 'yaml'):
            output = self.execute(deployment.LogDeployment, {'log': {
                'deployment_id': test_commands.STACK,
                'complete': False, 'lines': []}}, formatter)
            if formatter == 'table':
                self.assertIn('False', output)
            else:
                self.assertEqual({
                    'Deployment ID': test_commands.STACK,
                    'Complete': False, 'Lines': []}, output)

    def test_service_log_notices_preserve_formatted_rows(self):
        self.check_log_notices(logs.ShowLogs, 'logs')

    def test_deployment_log_notices_preserve_formatted_rows(self):
        self.check_log_notices(deployment.LogDeployment, 'log')

    def check_log_notices(self, cls, envelope):
        lines = [{'ts': 'now', 'stream': 'stdout', 'line': 'ready'}]
        cases = (
            ({'unavailable': 'Container is restarting.'}, []),
            ({'unavailable': True, 'note': 'Output is unavailable.'}, []),
            ({'unavailable': True, 'reason': 'Container has stopped.'}, []),
            ({'captured_at_failure': True,
              'captured_at': '2026-10-23T10:00:00Z',
              'deployment_id': test_commands.STACK,
              'note': 'Stored runtime output captured at failure time.'},
             lines),
            ({'note': 'Showing output from the last run.'}, lines),
            ({}, lines))
        for metadata, rows in cases:
            for formatter in ('table', 'json', 'yaml'):
                with self.subTest(command=cls, metadata=metadata,
                                  formatter=formatter):
                    log = dict(metadata, lines=rows,
                               deployment_id=test_commands.STACK,
                               complete=True)
                    output = self.execute(cls, {envelope: log}, formatter)
                    notice = self.app.stderr.getvalue()
                    if metadata.get('captured_at_failure'):
                        self.assertIn('captured at failure', notice)
                        self.assertIn(metadata['captured_at'], notice)
                        self.assertIn(test_commands.STACK, notice)
                        self.assertEqual(1, len(notice.splitlines()))
                    elif metadata:
                        self.assertIn(metadata.get('note') or
                                      metadata.get('reason') or
                                      metadata['unavailable'], notice)
                        self.assertEqual(1, len(notice.splitlines()))
                    else:
                        self.assertEqual('', notice)
                    if (formatter == 'table' and
                            (rows or cls is deployment.LogDeployment)):
                        self.assertIn('Line' if cls is logs.ShowLogs
                                      else 'Complete', output)
                    elif formatter == 'table':
                        self.assertEqual('', output.strip())
                    elif cls is logs.ShowLogs:
                        self.assertEqual([
                            {'Timestamp': 'now', 'Stream': 'stdout',
                             'Line': 'ready'}] if rows else [], output)
                    else:
                        self.assertEqual({
                            'Deployment ID': test_commands.STACK,
                            'Complete': True, 'Lines': rows}, output)

    def test_runtime_log_is_public_selectable_and_nullable(self):
        for runtime_log in (None, [{'line': 'failure', 'stream': 'stderr'}]):
            for formatter in ('table', 'json', 'yaml'):
                data = {'deployment': {'id': test_commands.STACK,
                                       'runtime_log': runtime_log}}
                output = self.execute(deployment.ShowDeployment, data,
                                      formatter, ['Runtime log'])
                if formatter == 'table':
                    self.assertIn('Runtime log', output)
                    self.assertIn('None' if runtime_log is None else 'failure',
                                  output)
                else:
                    self.assertEqual({'Runtime log': runtime_log}, output)
        output = self.execute(deployment.ShowDeployment,
                              {'deployment': {'id': test_commands.STACK}},
                              'json')
        self.assertNotIn('Runtime log', output)
