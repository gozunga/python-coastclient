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

"""Offline env input and masking tests."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import env


class EnvTest(base.BaseTestCase):
    def execute(self, entries, cls=env.SetEnv, stdin=None):
        self.api = mock.Mock()
        self.api.request.return_value = {'env': {
            'KEY': {'value': 'sensitive', 'secret': False}}}
        self.app = SimpleNamespace(stdout=io.StringIO(),
                                   client_manager=SimpleNamespace(
                                       coast=self.api))
        cmd = cls(self.app, None)
        args = cmd.get_parser('test').parse_args(
            ['--service', 'web'] + entries)
        with mock.patch('sys.stdin', stdin or io.StringIO('stdin value')), \
                mock.patch.object(env.utils, 'find_resource',
                                  return_value='service-id'), \
                mock.patch.object(env.utils, 'request',
                                  side_effect=lambda api, *a, **kw:
                                  api.request(*a, **kw)):
            cmd.take_action(args)
        self.assertNotIn('sensitive', self.app.stdout.getvalue())
        return self.api.request.call_args.kwargs['json']['env']

    def test_pairs_and_flags(self):
        values = self.execute(['KEY=a=b', 'EMPTY='])
        self.assertTrue(values == {'KEY': {'value': 'a=b'},
                                   'EMPTY': {'value': ''}})
        for flag in (True, False):
            prefix = '--' if flag else '--no-'
            values = self.execute(['KEY=a', 'OTHER=b', prefix + 'secret',
                                   prefix + 'build-time'])
            self.assertTrue(all(entry['secret'] is flag and
                                entry['build_time'] is flag
                                for entry in values.values()))

    def test_stdin_and_hidden_prompts(self):
        self.assertTrue(self.execute(['KEY'])['KEY']['value'] == 'stdin value')
        terminal = io.StringIO()
        terminal.isatty = lambda: True
        with mock.patch.object(env.getpass, 'getpass',
                               side_effect=['first', 'second']) as prompt:
            values = self.execute(['KEY', 'OTHER'], stdin=terminal)
        self.assertTrue(values['KEY']['value'] == 'first')
        self.assertTrue(values['OTHER']['value'] == 'second')
        self.assertEqual(2, prompt.call_count)

    def test_invalid_inputs_never_request_or_echo_values(self):
        for entries in ([], ['KEY', 'OTHER'], ['KEY=x', 'KEY=y'],
                        ['KEY=********']):
            with self.subTest(entries_count=len(entries)):
                self.assertRaises(exceptions.CommandError,
                                  self.execute, entries)
                self.api.request.assert_not_called()
                self.assertEqual('', self.app.stdout.getvalue())

    def test_dotenv_override_and_replace(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'input.env'
            path.write_text('# comment\n\nexport KEY="quoted"\n'
                            "OTHER='more=content'\nEMPTY=\n")
            values = self.execute(['--from-file', str(path), 'KEY=override'],
                                  cls=env.EnvReplace)
            self.assertTrue(values == {'KEY': {'value': 'override'},
                                       'OTHER': {'value': 'more=content'},
                                       'EMPTY': {'value': ''}})
            self.assertEqual(('/services/service-id/env', 'PUT'),
                             self.api.request.call_args.args)
            path.write_text('# comment\ninvalid=sensitive\nbad line\n')
            error = self.assertRaises(exceptions.CommandError, self.execute,
                                      ['--from-file', str(path)])
            self.assertIn('line 3', str(error))
            self.assertNotIn('sensitive', str(error))
            self.api.request.assert_not_called()
            path.write_text('KEY=********\n')
            self.assertRaises(exceptions.CommandError, self.execute,
                              ['--from-file', str(path)])

    def test_assignments_preserve_dotenv_markers(self):
        text = ('TOKEN=old # secret, runtime-only\n'
                'BUILD=old # build-time\n')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'input.env'
            path.write_text(text)
            for cls in (env.SetEnv, env.EnvReplace):
                for filename in (str(path), '-'):
                    for flags in ([], ['--secret']):
                        with self.subTest(command=cls.__name__,
                                          filename=filename, flags=flags):
                            values = self.execute([
                                '--from-file', filename,
                                'TOKEN=new', 'BUILD=new', 'OTHER=new'] + flags,
                                cls=cls, stdin=io.StringIO(text))
                            self.assertEqual({
                                'value': 'new', 'secret': True,
                                'build_time': False}, values['TOKEN'])
                            expected = {'value': 'new', 'build_time': True}
                            other = {'value': 'new'}
                            if flags:
                                expected['secret'] = True
                                other['secret'] = True
                            self.assertEqual(expected, values['BUILD'])
                            self.assertEqual(other, values['OTHER'])

    def test_replace_pairs_bare_and_flags(self):
        values = self.execute(['KEY', 'OTHER=', '--no-build-time', '--secret'],
                              cls=env.EnvReplace)
        self.assertTrue(values == {
            'KEY': {'value': 'stdin value',
                    'build_time': False, 'secret': True},
            'OTHER': {'value': '', 'build_time': False, 'secret': True}})
        self.assertEqual(1, self.api.request.call_count)

    def test_hidden_prompt_failure_and_stdin_mask(self):
        terminal = io.StringIO()
        terminal.isatty = lambda: True
        with mock.patch.object(env.getpass, 'getpass', side_effect=EOFError):
            self.assertRaises(exceptions.CommandError, self.execute,
                              ['KEY'], stdin=terminal)
        self.api.request.assert_not_called()
        self.assertRaises(exceptions.CommandError, self.execute,
                          ['KEY'], stdin=io.StringIO('********'))
        self.api.request.assert_not_called()

    def test_redirected_newline_handling_set_and_replace(self):
        cases = (('a\n', 'a'), ('a\r\n', 'a'), ('a\n\n', 'a\n'),
                 (' a \r\nb \t\n', ' a \r\nb \t'), ('a\r', 'a\r'))
        for cls in (env.SetEnv, env.EnvReplace):
            for number, (source, expected) in enumerate(cases):
                with self.subTest(command=cls.__name__, case=number):
                    values = self.execute(['KEY'], cls=cls,
                                          stdin=io.StringIO(source))
                    self.assertTrue(values['KEY']['value'] == expected)
                    self.assertEqual(1, self.api.request.call_count)

    def test_redirected_empty_and_mask_rejected_set_and_replace(self):
        for cls in (env.SetEnv, env.EnvReplace):
            for number, source in enumerate(('', '\n', '\r\n',
                                             '********\n', '********\r\n')):
                with self.subTest(command=cls.__name__, case=number):
                    error = self.assertRaises(
                        exceptions.CommandError, self.execute, ['KEY'],
                        cls=cls, stdin=io.StringIO(source))
                    if number < 3:
                        self.assertIn('KEY', str(error))
                        self.assertIn('Empty standard input', str(error))
                    else:
                        self.assertIn('display mask', str(error))
                    self.api.request.assert_not_called()
                    self.assertEqual('', self.app.stdout.getvalue())

    def test_stdin_dotenv_set_and_replace(self):
        for cls in (env.SetEnv, env.EnvReplace):
            with self.subTest(command=cls.__name__):
                stream = io.StringIO('# comment\nexport KEY="from file"\n'
                                     "OTHER='a=b'\nEMPTY=\n")
                values = self.execute(['--from-file', '-', 'KEY=override'],
                                      cls=cls, stdin=stream)
                self.assertTrue(values == {
                    'KEY': {'value': 'override'}, 'OTHER': {'value': 'a=b'},
                    'EMPTY': {'value': ''}})
                self.assertFalse(stream.closed)
                self.assertEqual(1, self.api.request.call_count)
                self.assertEqual(cls.method,
                                 self.api.request.call_args.args[1])

    def test_stdin_dotenv_conflict_does_not_consume_input(self):
        for cls in (env.SetEnv, env.EnvReplace):
            for terminal in (True, False):
                with self.subTest(command=cls.__name__, terminal=terminal):
                    stream = mock.Mock()
                    stream.isatty.return_value = terminal
                    with mock.patch.object(env.getpass, 'getpass') as prompt:
                        error = self.assertRaises(
                            exceptions.CommandError, self.execute,
                            ['--from-file', '-', 'KEY'], cls=cls, stdin=stream)
                    self.assertIn('--from-file -', str(error))
                    self.assertIn('bare KEY', str(error))
                    self.assertEqual([], stream.mock_calls)
                    prompt.assert_not_called()
                    self.api.request.assert_not_called()

    def test_stdin_dotenv_errors_set_and_replace(self):
        for cls in (env.SetEnv, env.EnvReplace):
            for number, source in enumerate(('# comment\nbad line\n',
                                             'KEY=********\n', '')):
                if cls is env.EnvReplace and not source:
                    continue
                with self.subTest(command=cls.__name__, case=number):
                    error = self.assertRaises(
                        exceptions.CommandError, self.execute,
                        ['--from-file', '-'], cls=cls,
                        stdin=io.StringIO(source))
                    if number == 0:
                        self.assertIn('line 2', str(error))
                    self.api.request.assert_not_called()
                    self.assertEqual('', self.app.stdout.getvalue())

    def test_abbreviations_and_old_replace_input_rejected(self):
        for cls in (env.SetEnv, env.EnvReplace):
            parser = cls(mock.Mock(), None).get_parser('test')
            self.assertFalse(parser.allow_abbrev)
            for option in ('--sec', '--file'):
                with mock.patch('sys.stderr', io.StringIO()):
                    self.assertRaises(SystemExit, parser.parse_args,
                                      ['--service', 'web', 'KEY=x', option])
