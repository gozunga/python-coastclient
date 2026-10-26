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

"""M5 list parent validation using offline HTTP fixtures."""
import io

from openstackclient import shell
from osc_lib import exceptions
from oslotest import base

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import service
from coastclient.tests.unit import test_commands


class ListParentsTest(base.BaseTestCase):
    set_up_api = test_commands.CommandTest.set_up_api
    register = test_commands.CommandTest.register
    execute = test_commands.CommandTest.execute
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)

    def setUp(self):
        super().setUp()
        self.set_up_api()
        self.app.stderr = io.StringIO()
        # Initialize discovery before counting parent requests.
        self.execute(service.ListApp, [])

    def test_parent_filters(self):
        group = test_commands.APPGROUP
        environment = test_commands.SERVER
        for cls in (service.ListApp, service.ListService):
            for noun, identity in (('appgroup', group),
                                   ('environment', environment)):
                path = noun + 's/' + identity
                for reference in (identity, identity.replace('-', '')):
                    with self.subTest(command=cls, noun=noun,
                                      reference=reference):
                        self.register('GET', path, status_code=404)
                        before = len(self.http.request_history)
                        cmd = cls(self.app, None)
                        args = cmd.get_parser('test').parse_args(
                            ['--' + noun, reference])
                        error = self.assertRaises(
                            exceptions.CommandError, cmd.take_action, args)
                        self.assertIn(
                            ("App group" if noun == 'appgroup' else
                             "Environment") + " '%s' not found." % reference,
                            str(error))
                        self.assertEqual(1, len(self.http.request_history) -
                                         before)
                self.register('GET', path, json={noun: {
                    'id': identity, 'appgroup_id': group}})
                self.register('GET', 'services', json={'services': []})
                before = len(self.http.request_history)
                self.execute(cls, ['--' + noun, identity])
                self.assertEqual(2, len(self.http.request_history) - before)

    def test_names_and_mismatch(self):
        group = test_commands.APPGROUP
        environment = test_commands.SERVER
        self.register('GET', 'stacks', json={'stacks': [
            {'id': test_commands.STACK}]})
        for cls in (service.ListApp, service.ListService):
            for noun in ('appgroup', 'environment'):
                self.register('GET', test_commands.APPGROUPS_PATH,
                              json={'appgroups': [{'id': group,
                                                   'name': 'group'}]})
                self.register('GET', 'appgroups/' + group + '/environments',
                              json={'environments': []})
                cmd = cls(self.app, None)
                args = cmd.get_parser('test').parse_args(
                    ['--' + noun, 'missing'])
                error = self.assertRaises(
                    exceptions.CommandError, cmd.take_action, args)
                self.assertIn(
                    ("App group" if noun == 'appgroup' else "Environment") +
                    " 'missing' not found.", str(error))
            self.register('GET', test_commands.APPGROUPS_PATH,
                          json={'appgroups': [{'id': group, 'name': 'group'}]})
            self.register('GET', 'appgroups/' + group + '/environments',
                          json={'environments': [
                              {'id': environment, 'name': 'env',
                               'appgroup_id': group}]})
            self.register('GET', 'services', json={'services': []})
            before = len(self.http.request_history)
            self.execute(cls, ['--appgroup', 'group', '--environment', 'env'])
            paths = [r.path for r in self.http.request_history[before:]]
            self.assertEqual(['/v1/stacks', '/v1/' +
                              test_commands.APPGROUPS_PATH,
                              '/v1/appgroups/' + group + '/environments',
                              '/v1/services'], paths)
            self.register('GET', 'appgroups/' + group,
                          json={'appgroup': {'id': group}})
            self.register('GET', 'environments/' + environment,
                          json={'environment': {'id': environment,
                                                'appgroup_id': 'other'}})
            cmd = cls(self.app, None)
            args = cmd.get_parser('test').parse_args(
                ['--appgroup', group, '--environment', environment])
            before = len(self.http.request_history)
            error = self.assertRaises(
                exceptions.CommandError, cmd.take_action, args)
            self.assertIn('does not belong to app group',
                          str(error))
            self.assertEqual(2, len(self.http.request_history) - before)

    def test_environment_name_in_other_group(self):
        group = test_commands.APPGROUP
        other = test_commands.STACK
        environment = test_commands.SERVER
        self.register('GET', 'stacks', json={'stacks': [
            {'id': test_commands.STACK}]})
        self.register('GET', test_commands.APPGROUPS_PATH,
                      json={'appgroups': [{'id': group, 'name': 'group'},
                                          {'id': other, 'name': 'other'}]})
        self.register('GET', 'appgroups/' + group + '/environments',
                      json={'environments': []})
        for cls in (service.ListApp, service.ListService):
            for elsewhere, message in (
                    ([{'id': environment, 'name': 'env',
                       'appgroup_id': other}],
                     "Environment 'env' does not belong to app group "
                     "'group'."),
                    ([{'id': environment, 'name': 'env',
                       'appgroup_id': other}] * 2,
                     "Environment 'env' does not belong to app group "
                     "'group'."),
                    ([], "Environment 'env' not found.")):
                with self.subTest(command=cls, elsewhere=len(elsewhere)):
                    self.register('GET',
                                  'appgroups/' + other + '/environments',
                                  json={'environments': elsewhere})
                    cmd = cls(self.app, None)
                    args = cmd.get_parser('test').parse_args(
                        ['--appgroup', 'group', '--environment', 'env'])
                    error = self.assertRaises(
                        exceptions.CommandError, cmd.take_action, args)
                    self.assertIn(message, str(error))

    def test_missing_environment_uuid_with_group(self):
        group = test_commands.APPGROUP
        environment = test_commands.SERVER
        self.register('GET', 'appgroups/' + group,
                      json={'appgroup': {'id': group}})
        self.register('GET', 'environments/' + environment, status_code=404)
        for cls in (service.ListApp, service.ListService):
            with self.subTest(command=cls):
                cmd = cls(self.app, None)
                args = cmd.get_parser('test').parse_args(
                    ['--appgroup', group, '--environment', environment])
                before = len(self.http.request_history)
                error = self.assertRaises(
                    exceptions.CommandError, cmd.take_action, args)
                self.assertIn("Environment '%s' not found." % environment,
                              str(error))
                self.assertEqual(2, len(self.http.request_history) - before)

    def test_events_of_deleted_resources(self):
        self.register('GET', 'events', json={'events': []})
        before = len(self.http.request_history)
        self.execute(catchup.EventList, [
            '--stack', test_commands.STACK,
            '--resource-id', test_commands.SERVER])
        self.assertEqual(1, len(self.http.request_history) - before)
        self.assertIn('deleted resources', catchup.EventList.__doc__)

    def test_shell_missing_parent_exits_one(self):
        auth = ['--os-auth-type', 'v3token', '--os-auth-url',
                'https://identity.example.com/v3', '--os-token', 'input-token',
                '--os-project-id', 'project', '--os-region-name', 'region']
        for kind in ('app', 'service'):
            for noun in ('appgroup', 'environment'):
                identity = test_commands.APPGROUP
                self.register('GET', noun + 's/' + identity, status_code=404)
                app = shell.OpenStackShell()
                app.stdout = io.StringIO()
                app.stderr = io.StringIO()
                self.assertEqual(1, app.run(
                    [*auth, 'coast', kind, 'list', '--' + noun, identity]))
                self.assertEqual('', app.stdout.getvalue())
