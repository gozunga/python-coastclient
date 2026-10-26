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

"""Open a container terminal, or run one command in a container."""
import argparse
import sys
from urllib import parse

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils
from coastclient.v1 import client
from coastclient.v1 import terminal


def _command_args(values):
    command_args = list(values or [])
    if command_args[:1] == ['--']:
        return command_args[1:]
    return command_args


def _wss(url):
    if not isinstance(url, str) or any(char in url for char in '\r\n'):
        return False
    parts = parse.urlsplit(url)
    return (parts.scheme == 'wss' and bool(parts.hostname) and
            not parts.username and not parts.password)


class _Exec(command.Command):
    """Open a container terminal, or run one command in it."""

    kind = None

    def get_parser(self, prog_name):
        parser = super().get_parser(prog_name)
        parser.add_argument(
            'target', metavar=self.kind.upper(),
            help='App or service name or ID.')
        parser.add_argument(
            'command', metavar='COMMAND', nargs=argparse.REMAINDER,
            help='Command and arguments. With no command, open an '
                 'interactive terminal. Put -- before a command when an '
                 'argument starts with a dash.')
        return parser

    def take_action(self, parsed_args):
        command_args = _command_args(parsed_args.command)
        api = self.app.client_manager.coast
        target = utils.find_resource(
            api, parsed_args.target, 'service', kind=self.kind)
        collection = 'apps' if self.kind == 'app' else 'services'
        path = '/%s/%s/console' % (collection, target)
        options = {'console_microversion': client.CONSOLE_MICROVERSION}
        if command_args:
            options['json'] = {'command': command_args}
        try:
            data = utils.request(api, path, 'POST', **options)
        except exceptions.CommandError as error:
            if 'HTTP 406' in str(error):
                raise exceptions.CommandError(
                    'A container terminal needs Coast API %s or later.'
                    % client.CONSOLE_MICROVERSION) from None
            raise
        url = data.get('url') if isinstance(data, dict) else None
        if not _wss(url):
            raise exceptions.CommandError('Invalid terminal address.')
        try:
            return terminal.run(
                url, api.adapter.session.verify,
                interactive=not command_args,
                stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                timeout=getattr(api.adapter.session, 'timeout', None))
        except terminal.TerminalError:
            raise exceptions.CommandError(
                'Terminal connection failed.') from None


class ExecApp(_Exec):
    """Open a terminal in an app, or run one command in it."""

    kind = 'app'


class ExecService(_Exec):
    """Open a terminal in a service, or run one command in it."""

    kind = 'service'
