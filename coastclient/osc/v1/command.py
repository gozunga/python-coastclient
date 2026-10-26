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

"""Shared presentation and command context for Coast commands."""
import argparse
import functools
import re

from osc_lib.command import command
from osc_lib import exceptions

from coastclient.osc.v1 import utils


CONTEXT = utils.CONTEXT


class Context:
    """Keep the addressed resource and the original parsed arguments."""

    def __init__(self, command, args):
        words = re.findall(r'[A-Z][a-z]*', type(command).__name__)
        verbs = {'Create', 'List', 'Show', 'Set', 'Delete', 'Add', 'Remove',
                 'Resize', 'Upgrade', 'Rollback', 'Start', 'Stop', 'Restart',
                 'Log', 'Cancel', 'Attach', 'Detach', 'Snapshot', 'Renew',
                 'Failover', 'Restore', 'Download', 'Replace', 'Unset',
                 'Verify', 'Regenerate', 'Exec', 'Defaults', 'Logs'}
        words = [word for word in words if word not in verbs]
        self.noun = ' '.join(words).lower() or {
            'Deploy': 'deployment', 'ShowLogs': 'logs'}.get(
                type(command).__name__, 'resource')
        self.args = vars(args).copy()

    def target(self, missing=False):
        key = self.noun.replace(' ', '_')
        argument = {'app': 'service', 'quota': 'project',
                    'ingress_rule': 'rule', 'app_webhook': 'service',
                    'stack_event': 'stack'}.get(key, key)
        value = (self.args.get(argument) or
                 self.args.get(argument + '_id') or
                 self.args.get('target') or self.args.get('name'))
        text = self.noun.capitalize()
        if value:
            text += ' ' + identifier(value)
        if missing:
            text += ' not found'
        for parent in ('stack', 'service', 'appgroup', 'environment'):
            value = self.args.get(parent) or self.args.get(parent + '_id')
            if value and parent != argument:
                text += ' in %s %s' % (parent, identifier(value))
                break
        return text


def identifier(value):
    """Quote names and retain the spelling supplied before UUID parsing."""
    original = getattr(value, 'original', value)
    try:
        utils.resource_id(original)
    except (ValueError, TypeError, argparse.ArgumentTypeError):
        return "'%s'" % original
    return str(original)


class CoastCommand:
    """Wrap actions once, including inherited route-generated actions."""

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.allow_abbrev = False
        if getattr(self, 'admin_scope', False):
            scope = parser.add_mutually_exclusive_group()
            scope.add_argument('--all-projects', action='store_true',
                               help='Read across projects (admin only). '
                                    'Resource references require UUIDs.')
            if self.admin_scope == 'list':
                scope.add_argument('--project', metavar='PROJECT',
                                   type=utils.project_id,
                                   help='Filter by project ID (admin only). '
                                        'Resource references require UUIDs.')
        parse_known_args = parser.parse_known_args

        def validate_references(args=None, namespace=None):
            parsed, remaining = parse_known_args(args, namespace)
            references = {'stack', 'server', 'appgroup', 'environment',
                          'service', 'database', 'domain', 'target', 'project'}
            for action in parser._actions:
                if ((action.dest in references or
                     action.dest.endswith('_id') or action.dest == 'rule') and
                        isinstance(getattr(parsed, action.dest, None), str) and
                        not getattr(parsed, action.dest).strip()):
                    parser.error('%s must not be empty.' % (
                        '/'.join(action.option_strings) or action.metavar or
                        action.dest.upper()))
            return parsed, remaining

        parser.parse_known_args = validate_references
        return parser

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        action = cls.take_action

        @functools.wraps(action)
        def take_action(self, args):
            outer = CONTEXT.get() is None
            token = CONTEXT.set(Context(self, args)) if outer else None
            try:
                if (getattr(self, 'admin_scope', None) == 'read' and
                        utils.scope_params(args)):
                    api = self.app.client_manager.coast
                    if tuple(map(int, api.microversion.split('.'))) < (1, 18):
                        raise exceptions.CommandError(
                            '--all-projects requires Coast API 1.18 '
                            'or later.')
                result = utils.api_call(action, self, args)
                if outer and isinstance(
                        self, (command.ShowOne, command.Lister)):
                    headings, values = result
                    headings = tuple(
                        field if getattr(self, 'raw_field_names', False) else
                        utils.label(field) for field in headings)
                    if isinstance(self, command.Lister):
                        order = [headings.index(key) for key in
                                 self.column_priority
                                 if key in headings]
                        order += [i for i in range(len(headings))
                                  if i not in order]
                        headings = tuple(headings[i] for i in order)
                        values = (tuple(row[i] for i in order)
                                  for row in values)
                    return headings, values
                return result
            finally:
                if token is not None:
                    CONTEXT.reset(token)

        cls.take_action = take_action


class Command(CoastCommand, command.Command):
    pass


class ShowOne(CoastCommand, command.ShowOne):
    pass


class Lister(CoastCommand, command.Lister):
    column_priority = ('ID', 'Name', 'Status')
