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

"""M5 list fields retain their types and order in every OSC formatter."""
import io
import json

from oslotest import base
import yaml

from coastclient.osc.v1 import catchup
from coastclient.osc.v1 import utils
from coastclient.tests.unit import test_commands


class ListColumnTest(base.BaseTestCase):
    def test_arbitrary_items(self):
        cases = (
            ([443, 80], '443, 80'),
            ([2.5, True, False, None, 'text', 1],
             '2.5, True, False, None, text, 1'),
            ([None], 'None'),
            ([], ''),
            (None, ''),
            ([{'ports': [443, 80]}, [1, {'enabled': True}], None],
             "{'ports': [443, 80]}, [1, {'enabled': True}], None"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                column = utils.ListColumn(value)
                self.assertEqual(expected, column.human_readable())
                self.assertIs(value, column.machine_readable())

    def test_roles_accept_mixed_items(self):
        column = utils.RolesColumn(['worker', 1, None, {'role': 'build'}])
        self.assertEqual("1, None, worker, {'role': 'build'}",
                         column.human_readable())

    def test_roles_remain_alphabetical(self):
        value = ['worker', 'build']
        column = utils.RolesColumn(value)
        self.assertEqual('build, worker', column.human_readable())
        self.assertEqual(['build', 'worker'], column.machine_readable())
        self.assertEqual(['worker', 'build'], value)


class ListOutputTest(base.BaseTestCase):
    set_up_api = test_commands.CommandTest.set_up_api
    execute = test_commands.CommandTest.execute
    register = test_commands.CommandTest.register
    assert_no_top_level_parent_lists = (
        test_commands.CommandTest.assert_no_top_level_parent_lists)

    def setUp(self):
        super().setUp()
        self.set_up_api()

    def test_template_show_integer_ports_default_table(self):
        self.register('GET', 'templates/next-image-transformation',
                      json={'template': {'ports': [443, 80]}})
        self.execute(catchup.TemplateShow,
                     ['next-image-transformation', '--stack',
                      test_commands.STACK], True)
        self.assertIn('Ports', self.app.stdout.getvalue())
        self.assertIn('443, 80', self.app.stdout.getvalue())

    def test_export_list_integer_artifacts_use_shared_formatter(self):
        self.register('GET', test_commands.STACK_PATH + '/exports',
                      json={'exports': [{'artifacts': [443, 80]}]})
        headings, rows = self.execute(catchup.ExportList,
                                      ['--stack', test_commands.STACK])
        artifacts = next(rows)[headings.index('Artifacts')]
        self.assertIsInstance(artifacts, utils.ListColumn)
        self.assertEqual('443, 80', artifacts.human_readable())
        self.assertEqual([443, 80], artifacts.machine_readable())

    def test_show_and_list_preserve_lists_in_all_formats(self):
        cases = (
            (catchup.TemplateShow,
             ['next-image-transformation', '--stack', test_commands.STACK],
             'templates/next-image-transformation', 'template', 'ports'),
            (catchup.ExportList, ['--stack', test_commands.STACK],
             test_commands.STACK_PATH + '/exports', 'exports', 'artifacts'),
        )
        for cls, argv, path, envelope, field in cases:
            for value in ([443, 80], [None, True, 2.5, {'ports': [80]}, [1]],
                          [], [{'ports': [443, 80]}]):
                row = {field: value}
                is_list = envelope == 'exports'
                self.register('GET', path,
                              json={envelope: [row] if is_list else row})
                for fmt in ('table', 'value', 'json', 'yaml'):
                    with self.subTest(command=cls.__name__, value=value,
                                      formatter=fmt):
                        self.app.stdout = io.StringIO()
                        self.execute(cls, [*argv, '-f', fmt,
                                           '-c', utils.label(field)], True)
                        output = self.app.stdout.getvalue()
                        if fmt in ('json', 'yaml'):
                            result = (json.loads(output) if fmt == 'json'
                                      else yaml.safe_load(output))
                            expected = {utils.label(field): value}
                            self.assertEqual([expected] if is_list else
                                             expected, result)
                        elif fmt == 'value':
                            self.assertEqual(str(value) + '\n', output)
                        else:
                            self.assertIn(utils.ListColumn(value).
                                          human_readable(), output)
