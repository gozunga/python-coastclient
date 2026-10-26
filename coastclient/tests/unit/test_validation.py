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

"""Consistent CLI validation and explicit clearing inputs."""
import argparse
import io
from types import SimpleNamespace
from unittest import mock

from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import env
from coastclient.osc.v1 import ingress
from coastclient.osc.v1 import quota
from coastclient.osc.v1 import resource
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.osc.v1 import utils


ID = '11111111-1111-4111-8111-111111111111'


class ValidationTest(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        self.api = mock.Mock()
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO(), stderr=io.StringIO())

    def test_integer_errors(self):
        for helper, invalid, message in (
                (utils.positive, ('0', '-1', '-5', '1.5', 'bad'),
                 'Use an integer greater than zero.'),
                (utils.nonnegative, ('-1', '-5', '1.5', 'bad'),
                 'Use zero or a positive integer.'),
                (quota.limit, ('-5', '1.5', 'bad', 'null'),
                 "Use -1 for unlimited, 0 or more, or 'default'."),
                (service.health_check_port,
                 ('0', '-1', '65536', '1.5', 'bad'),
                 'Use a port from 1 to 65535.')):
            for value in invalid:
                with self.subTest(helper=helper, value=value):
                    error = self.assertRaises(
                        argparse.ArgumentTypeError, helper, value)
                    self.assertEqual(message, str(error))
        self.assertEqual(1, utils.positive('1'))
        self.assertEqual(0, utils.nonnegative('0'))
        self.assertEqual(65535, service.health_check_port('65535'))

    def test_other_type_helpers_use_sentences(self):
        for helper, value, message in (
                (utils.resource_id, 'bad', 'Use an explicit resource UUID.'),
                (resource.boolean, 'bad', 'Use on or off.'),
                (utils.domain_suffix, 'null', 'Use a domain suffix.'),
                (env.key, 'bad-key', 'Use a valid env key.'),
                (ingress.port_range, '0',
                 'Use a port N or range N:M between 1 and 65535.'),
                (service.template_id, 'bad/id',
                 'Use a template ID such as postgresql.'),
                (service.positive_cpu, 'nan',
                 'Use a positive finite CPU limit.')):
            error = self.assertRaises(argparse.ArgumentTypeError,
                                      helper, value)
            self.assertEqual(message, str(error))
        for value in ('bad', 'kind=bad,plan=test', 'kind=vm'):
            error = self.assertRaises(argparse.ArgumentTypeError,
                                      stack.worker, value)
            self.assertEqual('Use kind=vm|baremetal,plan=PLAN[,image=IMAGE].',
                             str(error))

    def test_stack_clear_flags(self):
        for option, field in (('default-domain', 'default_domain'),
                              ('dns-zone', 'dns_zone_id'),
                              ('dns-trust', 'dns_trust_id')):
            cmd = catchup.StackSet(self.app, None)
            parser = cmd.get_parser('stack set')
            self.api.reset_mock()
            args = parser.parse_args([ID, '--no-' + option])
            with mock.patch.object(
                    utils, 'request', return_value={'stack': {}}) as request:
                cmd.take_action(args)
            self.assertEqual({'stack': {field: None}},
                             request.call_args.kwargs['json'])
            create = stack.CreateStack(self.app, None).get_parser('create')
            self.assertRaises(SystemExit, create.parse_args,
                              ['test', '--plan', 'control',
                               '--' + option, 'null'])
            for argv in ([ID, '--' + option, 'null'],
                         [ID, '--' + option, ID, '--no-' + option]):
                self.assertRaises(SystemExit, parser.parse_args, argv)
            help_text = parser.format_help()
            self.assertNotIn('null', help_text)
            self.assertIn('--no-' + option, help_text)

    def test_source_errors(self):
        for cls, choices, conflict in (
                (service.CreateApp, '--repo-url',
                 'Choose one of --repo-url, --image, --dockerfile or '
                 '--compose-file.'),
                (service.CreateService, '--template',
                 'Choose either --template or --image.')):
            cmd = cls(self.app, None)
            parser = cmd.get_parser('create')
            prefix = ['test', '--appgroup', ID, '--environment', ID,
                      '--server', ID]
            missing = ('Specify --repo-url, --image, --dockerfile or '
                       '--compose-file.' if cls is service.CreateApp else
                       'Specify %s or --image.' % choices)
            cases = [([], missing),
                     (['--image', 'example'], '--image requires --tag.'),
                     (['--tag', 'stable'], '--tag requires --image.')]
            for option in ('--image', '--tag'):
                cases.append(([choices, 'example', option, 'example'],
                              ('Choose either --repo-url or --image/--tag.'
                               if cls is service.CreateApp and option ==
                               '--tag' else conflict)))
            for options, message in cases:
                args = parser.parse_args(prefix + options)
                error = self.assertRaises(exceptions.CommandError,
                                          cmd.take_action, args)
                self.assertEqual(message, str(error))
            self.api.request.assert_not_called()
