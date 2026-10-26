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

"""API reference route inventory: no network and no deployment access."""
from importlib import metadata
import json
import os
from pathlib import Path
import re

from oslotest import base


FIXTURES = Path(__file__).parent / 'fixtures'
# Each exclusion documents why a public route has no command here.
EXCLUSIONS = {('GET', '/'): 'API version discovery',
              ('GET', '/v1'): 'API major version discovery'}


def routes_at(source):
    routes = set()
    for pattern in ('*.inc', '*.rst'):
        for path in source.glob(pattern):
            routes.update(re.findall(
                r'^\.\. rest_method::\s+(\w+)\s+(\S+)',
                path.read_text(encoding='utf-8'), re.MULTILINE))
    return routes


class RouteCoverageTest(base.BaseTestCase):
    def test_every_documented_route_has_an_installed_command(self):
        source = Path(os.environ.get(
            'COAST_API_REF', FIXTURES / 'api-ref'))
        routes = routes_at(source)
        self.assertTrue(routes, 'No Coast rest_method declarations found')
        commands = {ep.name.removeprefix('coast_'): ep.load()
                    for ep in metadata.entry_points(
                        group='openstack.coast.v1')}
        existing = json.loads((FIXTURES / 'existing-routes.json').read_text())
        covered = set()
        for name, route in existing.items():
            self.assertIn(name, commands)
            covered.add(tuple(route))
        for cls in commands.values():
            if getattr(cls, 'route', None):
                covered.add((cls.method, '/v1' + cls.route))
        self.assertEqual(set(), routes - covered - set(EXCLUSIONS),
                         'Add a CLI command or a reasoned exclusion')
        self.assertTrue(all(EXCLUSIONS.values()))

    def test_stack_upgrade_action_commands(self):
        commands = {ep.name: ep.load() for ep in metadata.entry_points(
            group='openstack.coast.v1')}
        self.assertNotIn('coast_stack_rollback', commands)
        for name in ('coast_stack_upgrade', 'coast_stack_upgrade_rollback'):
            cls = commands[name]
            self.assertEqual('POST', cls.method)
            self.assertEqual('/stacks/{stack_id}/action', cls.route)

    def test_database_routes_have_installed_commands(self):
        inventory = json.loads(
            (FIXTURES / 'existing-routes.json').read_text())
        commands = {ep.name.removeprefix('coast_'): ep.load()
                    for ep in metadata.entry_points(
                        group='openstack.coast.v1')}
        routes = routes_at(FIXTURES / 'api-ref')
        for suffix in ('engine_list', 'create', 'list', 'show', 'delete',
                       'start', 'stop', 'restart', 'logs', 'credentials_show',
                       'set', 'unset', 'backup_policy_show',
                       'backup_policy_set',
                       'backup_create', 'backup_list'):
            name = 'database_' + suffix
            self.assertIn(name, commands)
            self.assertIn(tuple(inventory[name]), routes)

    def test_parser_detects_new_route(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'new.inc').write_text(
                '.. rest_method:: PATCH /v1/new/{id}\n')
            self.assertEqual({('PATCH', '/v1/new/{id}')}, routes_at(path))
