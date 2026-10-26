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

"""Regression coverage for M5 Coast issue 106."""
import io
import tempfile
from types import SimpleNamespace
from unittest import mock

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import domain
from coastclient.osc.v1 import env
from coastclient.osc.v1 import service
from coastclient.osc.v1 import utils
from coastclient.tests.unit import test_commands


class Audit106Test(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.api = mock.Mock()
        self.app = SimpleNamespace(
            stdout=io.StringIO(),
            client_manager=SimpleNamespace(coast=self.api))

    def execute(self, cls, argv):
        cmd = cls(self.app, None)
        return cmd.take_action(cmd.get_parser('test').parse_args(argv))

    def test_domain_routing_writes(self):
        for cls, method in ((domain.AddDomain, 'POST'),
                            (domain.SetDomain, 'PATCH')):
            for record in ('A', 'AAAA', 'CNAME'):
                for primary in ('--primary', '--no-primary'):
                    argv = [test_commands.SERVER, '--service',
                            test_commands.SERVER, '--dns-trust', 'trust',
                            '--record-type', record, primary]
                    values = {'dns_trust_id': 'trust', 'record_type': record,
                              'primary': primary == '--primary'}
                    if cls is domain.SetDomain:
                        argv += ['--fqdn', 'web.example.com']
                        values['fqdn'] = 'web.example.com'
                    else:
                        values['fqdn'] = test_commands.SERVER
                    with mock.patch.object(utils, 'request', return_value={
                            'domain': values}) as request:
                        self.execute(cls, argv)
                    self.assertEqual(method, request.call_args.args[2])
                    self.assertEqual({'domain': values},
                                     request.call_args.kwargs['json'])
            parser = cls(self.app, None).get_parser('test')
            with mock.patch('sys.stderr', io.StringIO()):
                self.assertRaises(SystemExit, parser.parse_args,
                                  argv + ['--primary', '--no-primary'])
            self.assertIsNone(parser.parse_args([
                test_commands.SERVER, '--service',
                test_commands.SERVER]).primary)

    def test_empty_env_replace_and_set(self):
        with tempfile.NamedTemporaryFile(mode='w') as source:
            for filename in (source.name, '-'):
                argv = ['--service', test_commands.SERVER,
                        '--from-file', filename]
                with mock.patch('sys.stdin', io.StringIO('')), \
                        mock.patch.object(utils, 'request',
                                          return_value={'env': {}}) as request:
                    self.execute(env.EnvReplace, argv)
                    self.assertEqual({'env': {}},
                                     request.call_args.kwargs['json'])
                    request.reset_mock()
                    self.assertRaises(exceptions.CommandError, self.execute,
                                      env.SetEnv, argv)
                    request.assert_not_called()
        self.assertRaises(exceptions.CommandError, self.execute,
                          env.EnvReplace, ['--service', test_commands.SERVER])

    def test_redeploy_notice_on_writes_only(self):
        for method in ('POST', 'PATCH', 'PUT', 'DELETE', 'GET'):
            for value in ('true', 'false', ''):
                result = mock.Mock(headers={
                    'X-Coast-Redeploy-Required': value})
                self.api.request.return_value = result
                with mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                    self.assertIs(result, utils.response(
                        self.api, '/test', method))
                expected = method != 'GET' and value == 'true'
                self.assertEqual(expected, 'openstack coast deploy SERVICE'
                                 in err.getvalue())
                self.assertEqual('', self.app.stdout.getvalue())

    def test_sizes_are_positive(self):
        for cls, argv in (
                (catchup.StorageCreate, ['--stack', 'shop']),
                (catchup.VolumeCreate, ['--stack', 'shop', '--name', 'data']),
                (catchup.VolumeSet, ['volume-id', '--stack', 'shop'])):
            parser = cls(self.app, None).get_parser('test')
            for value in ('0', '-1', '1.5'):
                with mock.patch('sys.stderr', io.StringIO()):
                    self.assertRaises(SystemExit, parser.parse_args,
                                      argv + ['--size-gb', value])
            self.assertEqual(1, parser.parse_args(
                argv + ['--size-gb', '1']).size_gb)
            self.assertIn('positive integer', parser.format_help())
        self.assertIn('larger than the current one', parser.format_help())

    def test_help_explains_saved_settings_and_restore(self):
        for cls in (catchup.StorageCreate, catchup.StorageSet):
            help_text = ' '.join(cls(self.app, None).get_parser(
                'test').format_help().split())
            self.assertIn('Save', help_text)
            self.assertIn('applies on the next deploy', help_text)
        for cls in (service.SetApp, service.SetService):
            help_text = ' '.join(cls(self.app, None).get_parser(
                'test').format_help().split())
            self.assertIn('Name and description are saved immediately',
                          help_text)
            self.assertIn('other settings apply on the next deploy', help_text)
        parser = catchup.BackupRestore(self.app, None).get_parser('test')
        name = next(a for a in parser._actions if a.dest == 'name')
        self.assertIn('new service', name.help)
        self.assertIn('required for the default new_copy', name.help)
        self.assertIn('not allowed with overwrite', name.help)
