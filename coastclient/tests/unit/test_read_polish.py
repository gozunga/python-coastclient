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

"""Offline regression coverage for the M5 read command audit."""
from unittest import mock

from oslotest import base

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import deployment
from coastclient.osc.v1 import env
from coastclient.osc.v1 import logs
from coastclient.osc.v1 import service
from coastclient.osc.v1 import stack
from coastclient.osc.v1 import utils


class ReadPolishTest(base.BaseTestCase):
    def execute(self, cls, argv, rows, envelope):
        cmd = cls(mock.Mock(), None)
        args = cmd.get_parser('test').parse_args(argv)
        with mock.patch.object(utils, 'find_resource', return_value='id'):
            with mock.patch.object(utils, 'list_data', return_value=rows):
                with mock.patch.object(utils, 'request',
                                       return_value={envelope: rows}):
                    columns, output = cmd.take_action(args)
                    return columns, list(output)

    def test_workload_long_columns_and_missing_fields(self):
        for cls in (service.ListApp, service.ListService):
            columns, rows = self.execute(cls, ['--long'], [
                {'id': 'id', 'name': 'web', 'appgroup_id': 'group',
                 'server_id': 'worker', 'created_at': 'today'}], 'services')
            row = dict(zip(columns, rows[0]))
            self.assertEqual('group', row['App group ID'])
            self.assertEqual('worker', row['Server ID'])
            self.assertEqual('today', row['Created at'])
            self.assertEqual('', row['Updated at'])
            self.assertEqual('', row['Kind'])
            columns, rows = self.execute(cls, [], [], 'services')
            self.assertNotIn('Server ID', columns)
            self.assertEqual([], rows)

    def test_stack_plan_and_long_public_fields(self):
        for argv in ([], ['--long']):
            columns, rows = self.execute(stack.ListStack, argv, [
                {'id': 'id', 'plan': 'standard', 'description': 'Shop',
                 'default_domain': 'example.com', 'fault': 'large fault'},
                {'id': 'older'}], 'stacks')
            self.assertEqual('standard', rows[0][columns.index('Plan')])
            self.assertEqual('', rows[1][columns.index('Plan')])
            self.assertNotIn('Fault', columns)
            if argv:
                self.assertEqual('Shop', rows[0][columns.index('Description')])
                self.assertEqual('example.com',
                                 rows[0][columns.index('Default domain')])
                self.assertEqual('', rows[1][columns.index('Description')])
            else:
                self.assertNotIn('Description', columns)
                self.assertNotIn('Default domain', columns)

    def test_env_long_preserves_flags_and_masks_values(self):
        columns, rows = self.execute(env.ListEnv,
                                     ['--service', 'web', '--long'], {
                                         'MODE': {'value': 'private',
                                                  'build_time': False},
                                         'TOKEN': {'secret': True},
                                     }, 'env')
        values = [dict(zip(columns, row)) for row in rows]
        self.assertEqual('********', values[0]['Value'])
        self.assertIs(False, values[0]['Build time'])
        self.assertEqual('********', values[1]['Value'])
        self.assertIsNone(values[1]['Build time'])

    def test_deployment_long_keeps_fault_opt_in(self):
        columns, rows = self.execute(deployment.ListDeployment,
                                     ['--service', 'web', '--long'], [
                                         {'id': 'id', 'server_id': 'worker',
                                          'rollback_of': 'previous',
                                          'fault': 'failed'}], 'deployments')
        row = dict(zip(columns, rows[0]))
        self.assertEqual('worker', row['Server ID'])
        self.assertEqual('previous', row['Rollback of'])
        self.assertEqual('', row['Finished at'])
        self.assertNotIn('Fault', columns)
        columns, rows = self.execute(deployment.ListDeployment,
                                     ['--service', 'web', '--long',
                                      '-c', 'fault'], [{'fault': 'failed'}],
                                     'deployments')
        self.assertEqual('failed', dict(zip(columns, rows[0]))['Fault'])

    def test_log_help_distinguishes_api_limits_and_defaults(self):
        for cls, argv in ((logs.ShowLogs, ['--service', 'web']),
                          (catchup.ContainerLogs,
                           ['--service', 'web', 'c-web'])):
            parser = cls(mock.Mock(), None).get_parser('test')
            args = parser.parse_args(argv)
            self.assertIsNone(args.tail)
            self.assertIsNone(args.since)
            actions = {action.dest: action for action in parser._actions}
            self.assertIn('200', actions['tail'].help)
            self.assertIn('Zero returns no lines', actions['tail'].help)
            self.assertIn('omit', actions['since'].help.lower())
            if cls is catchup.ContainerLogs:
                self.assertIn('10000', actions['tail'].help)
                self.assertIn('naive', actions['since'].help)

    def test_file_mount_help_explains_content_and_container_restrictions(self):
        for cls in (catchup.FileMountCreate, catchup.FileMountSet):
            with self.subTest(command=cls.__name__):
                parser = cls(mock.Mock(), None).get_parser('test')
                help_text = ' '.join(parser.format_help().split())
                self.assertIn('at most 64 KiB', help_text)
                self.assertIn('UTF-8 content', help_text)
                self.assertIn('--resource-name RESOURCE_NAME', help_text)
                self.assertIn(
                    'Template container name; omit for a single-container '
                    'template. Invalid for non-template apps.', help_text)

    def test_backup_policy_help_explains_defaults_and_partial_updates(self):
        parser = catchup.BackupPolicySet(mock.Mock(), None).get_parser('test')
        help_text = ' '.join(parser.format_help().split())
        self.assertIn('omitted fields keep their saved values.', help_text)
        expected = {
            '--enabled ENABLED':
                'Scheduled backups: on or off; new policies default to off.',
            '--engine {postgres,mysql,mariadb,mongodb}':
                'Database engine: postgres, mysql, mariadb or mongodb; '
                'required for the first policy update.',
            '--container CONTAINER':
                'Database Compose container name; required for the first '
                'policy update.',
            '--hour HOUR':
                'UTC backup hour (0-23; new policy default: 2).',
            '--minute MINUTE':
                'UTC backup minute (0-59; new policy default: 0).',
            '--keep-daily KEEP_DAILY':
                'Daily backups to retain (0-365; new policy default: 14).',
            '--keep-weekly KEEP_WEEKLY':
                'Weekly backups to retain (0-104; new policy default: 8).',
        }
        for option, description in expected.items():
            with self.subTest(option=option):
                self.assertIn(option + ' ' + description, help_text)
