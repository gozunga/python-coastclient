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

"""Service environment variables with masked output."""
import argparse
import contextlib
import getpass
import re
import sys
import warnings

from osc_lib import exceptions

from coastclient.osc.v1 import command
from coastclient.osc.v1 import utils


def scoped(parser):
    parser.allow_abbrev = False
    parser.add_argument('--service', required=True, metavar='SERVICE',
                        help='App or service name or ID.')
    return parser


def key(value):
    if len(value) > 255 or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise argparse.ArgumentTypeError('Use a valid env key.')
    return value


def collection(api, args):
    return ('/services/' + utils.find_resource(api, args.service, 'service') +
            '/env')


def masked(values):
    return {name: dict(entry, value=(
        '********' if entry.get('secret') or
        entry.get('value') is not None else None))
        for name, entry in values.items()}


def output(app, data):
    values = utils.resource_data(data, 'env')
    utils.print_deleted(app, {'env': masked(values)}, 'env')


class Assignment(str):
    """A KEY[=VALUE] argument whose repr never shows the value.

    osc_lib logs the parsed arguments with --debug; this keeps values out.
    """

    def __repr__(self):
        name, separator, _value = self.partition('=')
        return repr(name + separator + ('<redacted>' if separator else ''))


def protect(parser):
    """Parse KEY=VALUE pairs anywhere and keep their values out of errors.

    Pairs may follow options (``A=1 --secret B=2``), and any parser error
    that would quote a pair shows ``KEY=<redacted>`` instead.
    """
    error = parser.error
    raw = []

    def parse_args(args=None, namespace=None):
        raw[:] = [arg for arg in (args or []) if '=' in arg
                  and not arg.startswith('-')]
        return parser.parse_intermixed_args(args, namespace)

    def redacted_error(message):
        for arg in sorted(raw, key=len, reverse=True):
            message = message.replace(arg, arg.partition('=')[0] +
                                      '=<redacted>')
        error(message)

    parser.parse_args = parse_args
    parser.error = redacted_error
    return parser


def assignment(value):
    key(value.partition('=')[0])
    return Assignment(value)


def read_values(args, allow_empty=False):
    supplied = {}
    bare = []
    for item in args.entries:
        name, separator, value = item.partition('=')
        if name in supplied:
            raise exceptions.CommandError('Duplicate env key.')
        supplied[name] = value
        if not separator:
            bare.append(name)
    if args.from_file == '-' and bare:
        raise exceptions.CommandError(
            '--from-file - cannot be combined with a bare KEY.')
    values = {}
    if args.from_file:
        values = read_dotenv(args.from_file, args.secret, args.build_time)
    terminal = sys.stdin.isatty() if bare else False
    if len(bare) > 1 and not terminal:
        raise exceptions.CommandError(
            'Redirected standard input supports only one bare KEY.')
    if not values and not supplied and not (allow_empty and args.from_file):
        raise exceptions.CommandError('Supply at least one env key.')
    for name in bare:
        if terminal:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', getpass.GetPassWarning)
                    supplied[name] = getpass.getpass('Value for %s: ' % name)
            except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
                raise exceptions.CommandError(
                    'Unable to read hidden env value.')
        else:
            value = sys.stdin.read()
            if value.endswith('\r\n'):
                value = value[:-2]
            elif value.endswith('\n'):
                value = value[:-1]
            if not value:
                raise exceptions.CommandError(
                    'Empty standard input for env key %s.' % name)
            supplied[name] = value
    flags = {field: getattr(args, field)
             for field in ('secret', 'build_time')
             if getattr(args, field) is not None}
    for name, value in supplied.items():
        reject_mask(name, value)
        values[name] = dict(values.get(name, flags), value=value)
    return values


def reject_mask(name, value, number=None):
    if value == '********':
        location = ' at line %d' % number if number is not None else ''
        raise exceptions.CommandError(
            'Env key %s%s: the display mask ******** means keep the stored '
            'value; omit this key from input.' % (name, location))


def read_dotenv(filename, secret=None, build_time=None):
    """Read dashboard-compatible dotenv without exposing values in errors."""
    try:
        source = (contextlib.nullcontext(sys.stdin) if filename == '-' else
                  open(filename, encoding='utf-8'))
        with source as stream:
            return parse_dotenv(stream.read(), secret, build_time)
    except (OSError, UnicodeError):
        raise exceptions.CommandError('Unable to read dotenv file.') from None


def parse_dotenv(text, secret=None, build_time=None):
    values = {}
    escapes = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\'}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, unquoted = line.partition('=')
        name = name.strip()
        try:
            key(name)
            if not separator:
                raise ValueError()
        except (argparse.ArgumentTypeError, ValueError):
            raise exceptions.CommandError(
                'Invalid dotenv entry at line %d.' % number) from None
        original = unquoted.strip()
        if original.startswith(('"', "'")):
            quote, i, out = original[0], 1, []
            while i < len(original):
                char = original[i]
                if quote == '"' and char == '\\' and i + 1 < len(original):
                    out.append(escapes.get(original[i + 1],
                                           '\\' + original[i + 1]))
                    i += 2
                    continue
                if char == quote:
                    break
                out.append(char)
                i += 1
            if i == len(original):
                raise exceptions.CommandError(
                    'Line %d: missing closing quote.' % number)
            value, comment = ''.join(out), original[i + 1:].strip()
            if comment and not comment.startswith('#'):
                raise exceptions.CommandError(
                    'Line %d: unexpected text after closing quote.' % number)
        else:
            parts = re.split(r'\s+#', unquoted, maxsplit=1)
            value = parts[0].strip()
            comment = '#' + parts[1] if len(parts) > 1 else ''
        if name in values:
            raise exceptions.CommandError(
                'Line %d: %s is defined twice.' % (number, name))
        flags = {field: flag for field, flag in (
            ('secret', secret), ('build_time', build_time))
                 if flag is not None}
        words = [word.strip().lower() for word in comment[1:].split(',')]
        found = set(words) if comment.startswith('#') and all(
            word in ('secret', 'runtime-only', 'build-time')
            for word in words) else set()
        if {'runtime-only', 'build-time'} <= found:
            raise exceptions.CommandError(
                'Line %d: %s cannot be both runtime-only and build-time.' %
                (number, name))
        markers = {}
        if 'secret' in found:
            markers['secret'] = True
        if found & {'runtime-only', 'build-time'}:
            markers['build_time'] = 'build-time' in found
        for field, flag in markers.items():
            if field in flags and flags[field] != flag:
                raise exceptions.CommandError(
                    'Line %d: %s marker contradicts --%s.' %
                    (number, name, field.replace('_', '-')))
        reject_mask(name, value, number)
        flags.update(markers)
        values[name] = dict(flags, value=value)
    return values


class SetEnv(command.Command):
    """Merge environment variables; deploy separately to apply them.

    Argument values appear in shell history, the process list and the
    --debug command line; use a bare KEY or --from-file for secrets.
    """

    method = 'PATCH'

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.allow_abbrev = False
        parser.usage = (
            '%(prog)s --service SERVICE [KEY=VALUE ...] [KEY] '
            '[--from-file FILE|-] [--secret|--no-secret] '
            '[--build-time|--no-build-time]')
        parser.add_argument('entries', nargs='*', type=assignment,
                            metavar='KEY=VALUE',
                            help='Pairs, or bare keys for hidden prompts on a '
                                 'terminal or one value from redirected '
                                 'stdin.')
        parser.add_argument('--from-file', metavar='FILE',
                            help='Read dotenv entries and per-key markers '
                                 'from FILE or stdin (-); '
                                 'stdin dotenv cannot be used with bare keys.')
        for field in ('secret', 'build_time'):
            options = parser.add_mutually_exclusive_group()
            flag = field.replace('_', '-')
            default = ('If omitted, keep the stored flag for existing keys; '
                       'new keys use the create default. '
                       if self.method == 'PATCH' else
                       'If omitted, use the create default for every key. ')
            default = ('Applies to keys without a corresponding file marker; '
                       'contradictions report the key and line. ') + default
            default += ('The create default is false.' if field == 'secret'
                        else 'The create default is on for non-template apps '
                        'and services at API 1.6+ and off otherwise; '
                        'template services are '
                        'runtime-only and reject --build-time. '
                        'Every variable is available at '
                        'runtime.')
            enabled = ('Store values write-only. ' if field == 'secret'
                       else 'Make variables available during builds. ')
            disabled = ('Store values as non-secret. '
                        if field == 'secret'
                        else 'Make variables runtime-only. ')
            options.add_argument('--' + flag, dest=field, action='store_true',
                                 help=enabled + default)
            options.add_argument('--no-' + flag, dest=field,
                                 action='store_false', help=disabled + default)
            parser.set_defaults(**{field: None})
        return protect(parser)

    def take_action(self, args):
        values = read_values(args, allow_empty=self.method == 'PUT')
        api = self.app.client_manager.coast
        data = utils.request(api, collection(api, args),
                             self.method, json={'env': values})
        output(self.app, data)


class EnvReplace(SetEnv):
    """Replace all env keys; keys not given are deleted.

    Argument values appear in shell history, the process list and the
    --debug command line; use bare keys or --from-file for secrets.
    """

    method = 'PUT'
    route = '/services/{service_id}/env'


class UnsetEnv(command.Command):
    """Remove an environment variable; deploy separately to apply it."""

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('key', type=key, metavar='KEY',
                            help='Variable name (up to 255 characters; '
                                 'letters, digits and underscores; cannot '
                                 'start with a digit).')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api, collection(api, args),
                             'PATCH', json={'env': {args.key: None}})
        output(self.app, data)


class ListEnv(command.Lister):
    """List env variables with masked values."""

    def get_parser(self, prog_name):
        parser = scoped(super().get_parser(prog_name))
        parser.add_argument('--long', action='store_true',
                            help='Include the stored build-time flag.')
        return parser

    def take_action(self, args):
        api = self.app.client_manager.coast
        data = utils.request(api, collection(api, args),
                             'GET')
        rows = masked(data['env'])
        fields = ('value', 'secret', 'version')
        columns = ('Key', 'Value', 'Secret', 'Version')
        if args.long:
            fields += ('build_time',)
            columns += ('Build time',)
        return (columns,
                ((name,) + utils.get_dict_properties(
                    {field: entry.get(field) for field in fields}, fields)
                 for name, entry in rows.items()))
