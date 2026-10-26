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

"""M5 named missing-resource errors for the real Coast API fault body."""
from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import appgroup
from coastclient.osc.v1 import env
from coastclient.osc.v1 import environment
from coastclient.osc.v1 import server
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.tests.unit import test_commands


class Generic404Test(base.BaseTestCase):
    set_up_api = test_commands.CommandTest.set_up_api
    register = test_commands.CommandTest.register
    execute = test_commands.CommandTest.execute
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)

    def setUp(self):
        super().setUp()
        self.set_up_api()

    def assert_missing(self, cls, argv, path, expected,
                       detail='Resource not found.'):
        self.register('GET', path, status_code=404, json={
            'faultcode': 'Client', 'faultstring': detail, 'debuginfo': None})
        error = self.assertRaises(exceptions.CommandError,
                                  self.execute, cls, argv)
        self.assertEqual(expected, str(error))

    def test_real_api_body_names_missing_resources(self):
        identity = test_commands.STACK
        for cls, argv, path, noun in (
                (stack.ShowStack, [identity], 'stacks/' + identity, 'Stack'),
                (appgroup.ListAppgroup, ['--stack', identity],
                 'stacks/' + identity + '/appgroups', 'Stack'),
                (server.ListServer, ['--stack', identity],
                 'stacks/' + identity + '/servers', 'Stack'),
                (service.ListApp, ['--appgroup', identity],
                 'appgroups/' + identity, 'App group'),
                (environment.ListEnvironment, ['--appgroup', identity],
                 'appgroups/' + identity + '/environments', 'App group'),
                (service.ListService, ['--environment', identity],
                 'environments/' + identity, 'Environment'),
                (env.ListEnv, ['--service', identity],
                 'services/' + identity + '/env', 'App or service')):
            with self.subTest(command=cls.__name__):
                self.assert_missing(cls, argv, path,
                                    "%s '%s' not found." % (noun, identity))

    def test_generic_faultstring_normalization(self):
        identity = test_commands.STACK
        for detail in ('Resource not found', ' RESOURCE NOT FOUND. ',
                       'Not Found', ' not FOUND. ', '', '   '):
            with self.subTest(detail=detail):
                self.assert_missing(stack.ShowStack, [identity],
                                    'stacks/' + identity,
                                    "Stack '%s' not found." % identity, detail)

    def test_specific_faultstring_is_preserved(self):
        identity = test_commands.STACK
        self.assert_missing(stack.ShowStack, [identity], 'stacks/' + identity,
                            'Coast API: Stack is missing. (HTTP 404).',
                            'Stack is missing.')
