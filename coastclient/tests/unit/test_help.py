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

"""Customer help must be complete for every installed Coast command."""
import argparse
from importlib import metadata
import re
from unittest import mock

from oslotest import base

from coastclient.osc.v1 import env


class HelpTest(base.BaseTestCase):
    def test_every_entry_point_has_customer_help(self):
        entries = list(metadata.entry_points(group='openstack.coast.v1'))
        self.assertTrue(entries, 'No Coast command entry points installed')
        # A help parser has no client manager or API available.
        app = mock.Mock(spec=['stdout', 'stderr'])
        for entry in entries:
            with self.subTest(command=entry.name):
                command = entry.load()(app, None)
                parser = command.get_parser(entry.name.replace('_', ' '))
                description = parser.description or ''
                summary = description.strip().splitlines()[0:1]
                self.assertTrue(summary, 'Missing command description')
                self.assertNotRegex(
                    summary[0],
                    r'(?i)^[\w -]+ ' +
                    re.escape(entry.name.rsplit('_', 1)[-1]) + r'\.$',
                    'Replace the noun/verb stub with a useful description')
                self.assertNotRegex(description, r'(?i)coolify|traefik')
                self.assertNotRegex(description, r'\bappgroup')
                for action in parser._actions:
                    # OSC hides its internal formatter options.
                    if action.help == argparse.SUPPRESS:
                        continue
                    self.assertTrue(
                        action.help and action.help.strip(),
                        '%s has no help' % (action.option_strings or
                                            action.dest))
                    self.assertNotRegex(action.help, r'(?i)coolify|traefik')
                    # Formatter arguments belong to OSC, not this plugin.
                    if action.container.title in (
                            'options', 'positional arguments'):
                        if action.dest != 'help':
                            self.assertTrue(action.help.rstrip().endswith('.'),
                                            action.help)
                        self.assertNotRegex(action.help, r'\bappgroup')
                        metavar = action.metavar or (
                            action.dest.upper() if action.option_strings
                            else action.dest)
                        for value in (metavar if isinstance(metavar, tuple)
                                      else (metavar,)):
                            if value != '<integer>':
                                self.assertEqual(value.upper(), value)
                        expected = {'--ports': 'PORTS',
                                    '--memory-limit-mb': 'MEMORY_LIMIT_MB',
                                    '--dns-trust': 'DNS_TRUST_ID',
                                    '--zone': 'DNS_ZONE_ID',
                                    '--dns-zone': 'DNS_ZONE_ID'}
                        if entry.name in ('coast_domain_add',
                                          'coast_domain_set'):
                            expected['--dns-trust'] = 'TRUST_ID'
                        for option in action.option_strings:
                            if option in expected:
                                self.assertEqual(expected[option], metavar)
                        if action.dest in ('service', 'target') and not (
                                action.option_strings):
                            noun = ('APP' if ('_app_' in entry.name or
                                              entry.name ==
                                              'coast_dockerfile_show') else
                                    'SERVICE')
                            self.assertEqual(noun, metavar)
                        if entry.name == 'coast_domain_add' and (
                                action.dest == 'name'):
                            self.assertEqual('DOMAIN', metavar)
                    if action.choices:
                        for choice in action.choices:
                            self.assertIn(str(choice), parser.format_help())
                if re.search(r'_(create|set)$', entry.name):
                    self.assertFalse(parser.allow_abbrev)

    def test_quota_project_metavars(self):
        entries = metadata.entry_points(group='openstack.coast.v1')
        for entry in entries:
            if entry.name in ('coast_quota_show', 'coast_quota_set',
                              'coast_quota_delete'):
                parser = entry.load()(mock.Mock(), None).get_parser(entry.name)
                project = next(action for action in parser._actions
                               if action.dest in ('project', 'project_id'))
                self.assertEqual('PROJECT', project.metavar)

    def test_env_pairs_explain_patch_and_put_defaults(self):
        for cls, omitted in (
                (env.SetEnv, 'keep the stored flag for existing keys'),
                (env.EnvReplace, 'use the create default for every key')):
            parser = cls(mock.Mock(), None).get_parser('test')
            actions = {a.option_strings[0]: a for a in parser._actions
                       if a.option_strings}
            for flag in ('secret', 'build-time'):
                enabled = actions['--' + flag].help
                disabled = actions['--no-' + flag].help
                self.assertIn(omitted, enabled)
                self.assertIn(omitted, disabled)
                self.assertEqual(enabled[enabled.index('If omitted'):],
                                 disabled[disabled.index('If omitted'):])
                self.assertIn('false' if flag == 'secret' else 'off', enabled)
                if flag == 'build-time':
                    self.assertIn('on for non-template apps and services '
                                  'at API 1.6+', enabled)
