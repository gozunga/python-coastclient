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

"""M5 resource writes support OSC formatters and reject missing envelopes."""
import io
from types import SimpleNamespace
from unittest import mock

import fixtures
from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import command
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import ingress
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.osc.v1 import utils
from coastclient.tests.unit import labels
from coastclient.tests.unit import test_catchup
from coastclient.tests.unit import test_phase4
from coastclient.tests.unit import test_phase5
from coastclient.tests.unit import test_phase6


CASES = [case for cases in (test_phase4.CASES, test_phase5.CASES,
                            test_catchup.CASES) for case in cases
         if case[2] in ('POST', 'PATCH', 'PUT') and
         issubclass(case[0], command.ShowOne)]
CASES += [(service.CreateService,
           [*test_phase4.CREATE[:7], '--template', 'postgresql'],
           'POST', '', 'service', {})]
CASES += [(test_phase6.quota.SetQuota,
           ['--project', test_phase6.PROJECT, '--apps', '0'],
           'PUT', '', 'quota', {})]


class WriteOutputTest(base.BaseTestCase):

    def setUp(self):
        super().setUp()
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=mock.Mock()),
            stdout=io.StringIO(), stderr=io.StringIO())
        self.useFixture(fixtures.MockPatchObject(utils, 'request'))
        self.useFixture(fixtures.MockPatchObject(
            utils, 'resolve_references', return_value=None))
        self.useFixture(fixtures.MockPatchObject(
            utils, 'find_resource', return_value=test_phase4.SERVICE))

    def test_writes_accept_formatters_with_shared_headings(self):
        resource = {'id': 'accepted-id', 'status': 'PENDING',
                    'pending_changes': False}
        for cls, argv, method, path, envelope, body in CASES:
            with self.subTest(command=cls.__name__):
                cmd = cls(self.app, None)
                args = cmd.get_parser('test').parse_args(
                    [*argv, '-f', 'json', '-c', 'ID'])
                utils.request.return_value = {envelope: resource}
                with mock.patch('sys.stdin', io.StringIO('content')):
                    columns, values = cmd.take_action(args)
                self.assertEqual(labels.labelled(resource),
                                 dict(zip(columns, values)))
                with mock.patch('sys.stdin', io.StringIO('content')):
                    cmd.run(args)
                self.assertEqual('{\n  "ID": "accepted-id"\n}\n',
                                 self.app.stdout.getvalue())
                self.app.stdout = io.StringIO()
                args = cmd.get_parser('test').parse_args(argv)
                with mock.patch('sys.stdin', io.StringIO('content')):
                    cmd.run(args)
                self.assertIn('| Field', self.app.stdout.getvalue())
                self.assertIn('accepted-id', self.app.stdout.getvalue())
                self.app.stdout = io.StringIO()

    def test_writes_require_resource_envelopes(self):
        for cls, argv, method, path, envelope, body in CASES:
            for response in (None, [], [{'id': 'unexpected'}], {},
                             {'other': {}}, {envelope: None}):
                with self.subTest(command=cls.__name__, response=response):
                    cmd = cls(self.app, None)
                    args = cmd.get_parser('test').parse_args(argv)
                    utils.request.return_value = response
                    with mock.patch('sys.stdin', io.StringIO('content')):
                        error = self.assertRaises(
                            exceptions.CommandError, cmd.take_action, args)
                    self.assertEqual('Invalid Coast API response.', str(error))
        self.assertEqual('', self.app.stdout.getvalue())

    def test_create_requires_worker_before_request(self):
        for cls, source in ((service.CreateApp, ['--image', 'nginx']),
                            (service.CreateService,
                             ['--template', 'postgresql'])):
            parser = cls(self.app, None).get_parser('test')
            self.assertFalse(parser.allow_abbrev)
            argv = ['web', '--appgroup', test_phase4.PARENT,
                    '--environment', test_phase4.PARENT, *source]
            error = self.assertRaises(SystemExit, parser.parse_args, argv)
            self.assertEqual(2, error.code)
            self.assertIn("Worker server name or ID in the app group's stack.",
                          next(action.help for action in parser._actions
                               if action.dest == 'server'))
        utils.request.assert_not_called()

    def test_create_help_names_the_resource(self):
        cases = (
            (stack.CreateStack, 'stack', True),
            (appgroup.CreateAppgroup, 'app group', True),
            (environment.CreateEnvironment, 'environment', True),
            (service.CreateApp, 'app', True),
            (service.CreateService, 'service', True),
            (catchup.StorageCreate, 'storage', False),
            (catchup.VolumeCreate, 'volume', False),
        )
        for cls, noun, has_description in cases:
            with self.subTest(command=cls.__name__):
                parser = cls(self.app, None).get_parser('test')
                help_text = ' '.join(parser.format_help().split())
                self.assertIn('Name for the new %s.' % noun, help_text)
                if has_description:
                    self.assertIn('%s description.' % noun.capitalize(),
                                  help_text)
                self.assertNotIn('Name for the new resource.', help_text)
                self.assertNotIn('Resource description.', help_text)
        parser = ingress.CreateIngressRule(self.app, None).get_parser('test')
        self.assertIn('Ingress rule description.',
                      ' '.join(parser.format_help().split()))

    def test_stack_plan_help_names_the_full_command(self):
        parser = stack.CreateStack(self.app, None).get_parser('test')
        self.assertIn('Operator plan name from openstack coast plan list.',
                      ' '.join(parser.format_help().split()))
