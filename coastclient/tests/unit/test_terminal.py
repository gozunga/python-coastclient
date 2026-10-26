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

"""Container terminal: masked frames, a fake peer, and exec commands."""
import base64
import hashlib
import io
import json
import os
import pty
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import termios
import threading
from types import SimpleNamespace
from unittest import mock
from urllib import parse

import fixtures
from keystoneauth1 import fixture as ks_fixture
from keystoneauth1.identity import v3
from keystoneauth1 import session
from osc_lib import exceptions
from oslotest import base
from requests_mock.contrib import fixture

from coastclient.osc.v1 import terminal as commands
from coastclient.v1 import client
from coastclient.v1 import terminal

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
APP = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
SERVICE = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
ROOT = 'https://app.example.com/'
BASE = ROOT + 'v1/'


def server_frame(opcode, payload, fin=True):
    """Unmasked server frame."""
    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    if length < 126:
        header = bytes((first, length))
    elif length < 65536:
        header = bytes((first, 126)) + struct.pack('!H', length)
    else:
        header = bytes((first, 127)) + struct.pack('!Q', length)
    return header + payload


def _header(head, name):
    prefix = name.lower() + ':'
    for line in head.split('\r\n')[1:]:
        if line.lower().startswith(prefix):
            return line.split(':', 1)[1].strip()
    return ''


def read_http(conn):
    data = bytearray()
    while b'\r\n\r\n' not in data:
        chunk = conn.recv(4096)
        if not chunk:
            raise AssertionError('handshake closed')
        data += chunk
        if len(data) > 8192:
            raise AssertionError('handshake too large')
    head, rest = bytes(data).split(b'\r\n\r\n', 1)
    return head.decode('iso-8859-1'), bytearray(rest)


def accept_value(key):
    digest = hashlib.sha1(
        (key + GUID).encode('ascii'), usedforsecurity=False).digest()
    return base64.b64encode(digest).decode('ascii')


def send_upgrade(conn, key, accept=None):
    if accept is None:
        accept = accept_value(key)
    conn.sendall((
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        'Sec-WebSocket-Accept: %s\r\n'
        '\r\n' % accept).encode('ascii'))


def read_client_frames(conn, count, buf=None):
    """Read count masked client frames."""
    buf = bytearray() if buf is None else buf
    frames = []
    conn.settimeout(5)
    while len(frames) < count:
        if len(buf) >= 2 and not (buf[1] & 0x80):
            raise AssertionError('client frame is not masked')
        frame = terminal.pop_frame(buf, allow_mask=True)
        if frame is not None:
            frames.append(frame)
            continue
        chunk = conn.recv(65536)
        if not chunk:
            raise AssertionError('client closed early')
        buf += chunk
    return frames, buf


class FakePeer(threading.Thread):
    """One-shot WebSocket peer on 127.0.0.1."""

    def __init__(self, handler, tls=None):
        super().__init__()
        self.handler = handler
        self.tls = tls
        self.error = None
        self.request = ''
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.conn = None
        self.start()

    def url(self, scheme='ws', token='console-token'):
        return '%s://127.0.0.1:%d/?token=%s' % (scheme, self.port, token)

    def run(self):
        try:
            self.sock.settimeout(5)
            conn, _ = self.sock.accept()
            self.conn = conn
            conn.settimeout(5)
            if self.tls:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(self.tls[0], self.tls[1])
                conn = context.wrap_socket(conn, server_side=True)
                self.conn = conn
            head, rest = read_http(conn)
            self.request = head
            key = _header(head, 'Sec-WebSocket-Key')
            if getattr(self.handler, 'bad_accept', False):
                send_upgrade(conn, key, accept='not-the-accept-value')
            else:
                send_upgrade(conn, key)
            self.handler(conn, rest)
        except Exception as exc:  # pylint: disable=broad-except
            self.error = exc
        finally:
            self._close(self.conn)
            self._close(self.sock)

    def close(self):
        self._close(self.conn)
        self._close(self.sock)
        self.join(5)
        if self.is_alive():
            raise AssertionError('peer thread is still running')

    @staticmethod
    def _close(sock):
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass


def _openssl(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, 'w', encoding='ascii') as handle:
        handle.write(body)
    return path


def _ca_config(directory, name, common_name):
    """CA config with key usage. Python 3.14 rejects a CA without it."""
    return _openssl(directory, name, (
        '[req]\n'
        'distinguished_name=dn\n'
        'x509_extensions=v3_ca\n'
        'prompt=no\n'
        '[dn]\n'
        'CN=%s\n'
        '[v3_ca]\n'
        'basicConstraints=critical,CA:TRUE\n'
        'keyUsage=critical,keyCertSign,cRLSign\n'
        'subjectKeyIdentifier=hash\n'
        'authorityKeyIdentifier=keyid:always,issuer\n' % common_name))


def make_certs(directory):
    """Create a test CA and a 127.0.0.1 certificate."""
    ca_key = os.path.join(directory, 'ca.key')
    ca_crt = os.path.join(directory, 'ca.crt')
    other = os.path.join(directory, 'other.crt')
    key = os.path.join(directory, 'server.key')
    csr = os.path.join(directory, 'server.csr')
    crt = os.path.join(directory, 'server.crt')
    ca_cnf = _ca_config(directory, 'ca.cnf', 'TestCA')
    other_cnf = _ca_config(directory, 'other.cnf', 'OtherCA')
    server_cnf = _openssl(directory, 'server.cnf', (
        '[v3]\n'
        'basicConstraints=CA:FALSE\n'
        'keyUsage=critical,digitalSignature,keyEncipherment\n'
        'extendedKeyUsage=serverAuth\n'
        'subjectAltName=IP:127.0.0.1,DNS:localhost\n'))
    quiet = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    subprocess.check_call([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', ca_key,
        '-out', ca_crt, '-days', '3650', '-nodes', '-config', ca_cnf],
        **quiet)
    subprocess.check_call([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout',
        os.path.join(directory, 'other.key'), '-out', other, '-days', '3650',
        '-nodes', '-config', other_cnf], **quiet)
    subprocess.check_call([
        'openssl', 'req', '-newkey', 'rsa:2048', '-keyout', key, '-out', csr,
        '-nodes', '-subj', '/CN=127.0.0.1'], **quiet)
    subprocess.check_call([
        'openssl', 'x509', '-req', '-in', csr, '-CA', ca_crt, '-CAkey',
        ca_key, '-CAcreateserial', '-out', crt, '-days', '3650',
        '-extfile', server_cnf, '-extensions', 'v3'], **quiet)
    return ca_crt, other, crt, key


class CodecTest(base.BaseTestCase):

    def test_masked_round_trip_and_rejected_server_mask(self):
        payload = b'x' * 200
        buf = bytearray(terminal.encode_frame(terminal.OP_BINARY, payload))
        self.assertTrue(buf[1] & 0x80)
        fin, opcode, data = terminal.pop_frame(buf, allow_mask=True)
        self.assertTrue(fin)
        self.assertEqual(terminal.OP_BINARY, opcode)
        self.assertEqual(payload, data)
        self.assertFalse(buf)
        plain = bytearray(server_frame(terminal.OP_BINARY, payload))
        self.assertEqual(payload, terminal.pop_frame(plain)[2])
        masked = bytearray(bytes((0x82, 0x80)) + b'\0\0\0\0')
        self.assertRaises(terminal.TerminalError, terminal.pop_frame, masked)
        reserved = bytearray(bytes((0xF2, 0x00)))
        self.assertRaises(terminal.TerminalError, terminal.pop_frame,
                          reserved)

    def test_partial_text_frame_waits_for_last_byte(self):
        frame = server_frame(
            terminal.OP_TEXT, b'{"type":"exit","code":2}')
        buf = bytearray()
        for byte in frame[:-1]:
            buf.append(byte)
            self.assertIsNone(terminal.pop_frame(buf))
        buf.append(frame[-1])
        _fin, opcode, payload = terminal.pop_frame(buf)
        self.assertEqual(terminal.OP_TEXT, opcode)
        self.assertEqual(2, json.loads(payload)['code'])

    def test_tls_context_follows_session_verify(self):
        disabled = terminal.tls_context(False)
        self.assertEqual(ssl.CERT_NONE, disabled.verify_mode)
        self.assertFalse(disabled.check_hostname)
        required = terminal.tls_context(True)
        self.assertEqual(ssl.CERT_REQUIRED, required.verify_mode)
        self.assertTrue(required.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED,
                         terminal.tls_context(None).verify_mode)
        self.assertRaises(terminal.TerminalError, terminal.tls_context, '')


class SessionTest(base.BaseTestCase):

    def exercise(self, peer, func):
        error = None
        result = None
        try:
            result = func()
        except Exception as exc:  # pylint: disable=broad-except
            error = exc
        peer.close()
        if peer.error:
            raise peer.error
        if error:
            raise error
        return result

    def test_one_off_streams_output_and_does_not_send_input(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        original = termios.tcgetattr(slave)
        os.write(master, b'secret-input')
        stdout = io.BytesIO()
        payload = b'out' + (b'y' * 180)

        def handler(conn, rest):
            conn.settimeout(0.3)
            try:
                early = conn.recv(1)
            except socket.timeout:
                early = b''
            if early:
                raise AssertionError('one-off client sent a frame')
            conn.settimeout(5)
            conn.sendall(server_frame(terminal.OP_BINARY, payload))
            conn.sendall(server_frame(
                terminal.OP_TEXT, b'{"type":"exit","code":9}'))

        peer = FakePeer(handler)
        stdin = os.fdopen(slave, 'rb', buffering=0)
        self.addCleanup(stdin.close)
        code = self.exercise(peer, lambda: terminal.run(
            peer.url(), True, interactive=False, stdin=stdin, stdout=stdout,
            timeout=5, session_timeout=5))
        self.assertEqual(9, code)
        self.assertEqual(payload, stdout.getvalue())
        self.assertEqual(original, termios.tcgetattr(slave))
        self.assertIn('token=console-token', peer.request.split('\r\n')[0])
        self.assertIn('127.0.0.1:%d' % peer.port, peer.request)
        self.assertNotIn('X-Auth-Token', peer.request)

    def test_interactive_resize_stdin_ping_and_fragments(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b'ls\n')
        os.close(write_fd)
        stdout = io.BytesIO()
        blob = b'B' * 200

        def handler(conn, rest):
            frames, buf = read_client_frames(conn, 2, rest)
            self.assertEqual(terminal.OP_TEXT, frames[0][1])
            self.assertEqual({'type': 'resize', 'cols': 40, 'rows': 12},
                             json.loads(frames[0][2]))
            self.assertEqual(terminal.OP_BINARY, frames[1][1])
            self.assertEqual(b'ls\n', frames[1][2])
            conn.sendall(server_frame(terminal.OP_PING, b'xyz'))
            pong, buf = read_client_frames(conn, 1, buf)
            self.assertEqual(terminal.OP_PONG, pong[0][1])
            self.assertEqual(b'xyz', pong[0][2])
            conn.sendall(server_frame(terminal.OP_BINARY, blob))
            conn.sendall(server_frame(terminal.OP_BINARY, b'AB', fin=False))
            conn.sendall(server_frame(
                terminal.OP_CONTINUATION, b'CD', fin=True))
            conn.sendall(server_frame(
                terminal.OP_TEXT, b'{"type":"ex', fin=False))
            conn.sendall(server_frame(
                terminal.OP_CONTINUATION, b'it","code":4}', fin=True))

        peer = FakePeer(handler)
        stdin = os.fdopen(read_fd, 'rb', buffering=0)
        self.addCleanup(stdin.close)
        code = self.exercise(peer, lambda: terminal.run(
            peer.url(), True, interactive=True, stdin=stdin, stdout=stdout,
            timeout=5, session_timeout=5, winsize=(40, 12)))
        self.assertEqual(4, code)
        self.assertEqual(blob + b'ABCD', stdout.getvalue())

    def test_raw_tty_restores_and_sigwinch_sends_resize(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        original = termios.tcgetattr(slave)
        stdout = io.BytesIO()

        def handler(conn, rest):
            frames, _buf = read_client_frames(conn, 1, rest)
            self.assertEqual(terminal.OP_TEXT, frames[0][1])
            flags = termios.tcgetattr(slave)[3]
            if flags & termios.ECHO or flags & termios.ICANON:
                raise AssertionError('tty stayed cooked')
            os.kill(os.getpid(), signal.SIGWINCH)
            second, _buf = read_client_frames(conn, 1)
            self.assertEqual(terminal.OP_TEXT, second[0][1])
            self.assertEqual('resize', json.loads(second[0][2])['type'])
            conn.sendall(server_frame(terminal.OP_BINARY, b'ok'))
            conn.sendall(server_frame(
                terminal.OP_TEXT, b'{"type":"exit","code":7}'))

        peer = FakePeer(handler)
        stdin = os.fdopen(slave, 'rb', buffering=0)
        self.addCleanup(stdin.close)
        code = self.exercise(peer, lambda: terminal.run(
            peer.url(), True, interactive=True, stdin=stdin, stdout=stdout,
            timeout=5, session_timeout=5, winsize=(20, 5)))
        self.assertEqual(7, code)
        self.assertEqual(b'ok', stdout.getvalue())
        self.assertEqual(original, termios.tcgetattr(slave))

    def test_tty_guard_restores_on_exit_stop_and_exception(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        original = termios.tcgetattr(slave)
        guard = terminal.TtyGuard(slave)
        guard.__enter__()
        self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
        with mock.patch('coastclient.v1.terminal.os.kill') as kill:
            guard._on_exit(signal.SIGTERM, None)
        kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
        self.assertEqual(original, termios.tcgetattr(slave))
        guard.__exit__(None, None, None)

        guard = terminal.TtyGuard(slave)
        guard.__enter__()

        def fake_kill(pid, sig):
            self.assertEqual(signal.SIGTSTP, sig)
            self.assertEqual(original, termios.tcgetattr(slave))

        with mock.patch('coastclient.v1.terminal.os.kill',
                        side_effect=fake_kill):
            guard._on_stop(signal.SIGTSTP, None)
        self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
        guard.__exit__(None, None, None)
        self.assertEqual(original, termios.tcgetattr(slave))
        try:
            with terminal.TtyGuard(slave):
                raise RuntimeError('boom')
        except RuntimeError:
            pass
        self.assertEqual(original, termios.tcgetattr(slave))

    def test_bad_upgrade_and_refused_port_hide_the_token(self):
        def handler(conn, rest):
            return None

        handler.bad_accept = True
        peer = FakePeer(handler)
        error = None
        try:
            self.exercise(peer, lambda: terminal.run(
                peer.url(token='sekret'), True, timeout=5,
                session_timeout=5))
        except terminal.TerminalError as exc:
            error = exc
        self.assertIsNotNone(error)
        self.assertNotIn('sekret', str(error))
        refused = self.assertRaises(
            terminal.TerminalError, terminal.run,
            'wss://127.0.0.1:1/?token=sekret', True, timeout=2,
            session_timeout=2)
        self.assertNotIn('sekret', str(refused))
        invalid = self.assertRaises(
            terminal.TerminalError, terminal.run,
            'http://127.0.0.1/?token=sekret', True)
        self.assertEqual('Invalid terminal address.', str(invalid))
        self.assertNotIn('sekret', str(invalid))

    def _run_expect_error(self, peer, verify, token):
        error = None
        try:
            terminal.run(
                peer.url(scheme='wss', token=token), verify, timeout=5,
                session_timeout=5, stdout=io.BytesIO())
        except terminal.TerminalError as exc:
            error = exc
        finally:
            peer.close()
        self.assertIsNotNone(error)
        self.assertNotIn(token, str(error))
        return error

    def test_tls_uses_the_session_ca_bundle(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        ca_crt, other, crt, key = make_certs(directory)

        def handler(conn, rest):
            conn.sendall(server_frame(
                terminal.OP_TEXT, b'{"type":"exit","code":0}'))

        peer = FakePeer(handler, tls=(crt, key))
        code = self.exercise(peer, lambda: terminal.run(
            peer.url(scheme='wss'), ca_crt, timeout=5, session_timeout=5,
            stdout=io.BytesIO()))
        self.assertEqual(0, code)

        def fail_handler(conn, rest):
            conn.sendall(server_frame(
                terminal.OP_TEXT, b'{"type":"exit","code":0}'))

        wrong = FakePeer(fail_handler, tls=(crt, key))
        rejected = self._run_expect_error(wrong, other, 'sekret')
        self.assertNotIn('sekret', str(rejected))
        system = FakePeer(fail_handler, tls=(crt, key))
        self._run_expect_error(system, True, 'console-token')
        opened = FakePeer(fail_handler, tls=(crt, key))
        code = self.exercise(opened, lambda: terminal.run(
            opened.url(scheme='wss'), False, timeout=5, session_timeout=5,
            stdout=io.BytesIO()))
        self.assertEqual(0, code)


class ExecCommandTest(base.BaseTestCase):

    def setUp(self):
        super().setUp()
        self.http = self.useFixture(fixture.Fixture())
        token = ks_fixture.V3Token(project_id='project')
        token.add_service('app-hosting', name='coast').add_endpoint(
            interface='public', region='region', url=ROOT)
        auth_url = 'https://identity.example.com/v3'
        self.http.post(auth_url + '/auth/tokens', json=token,
                       headers={'X-Subject-Token': 'test-token'})
        self.session = session.Session(auth=v3.Token(
            auth_url=auth_url, token='input-token', project_id='project'))
        self.http.get(ROOT, json={'versions': [{
            'id': 'v1', 'status': 'CURRENT', 'min_version': '1.0',
            'max_version': '1.1', 'links': [{'rel': 'self', 'href': BASE}]}]})
        self.api = client.Client(self.session, region_name='region',
                                 microversion='1.0')
        self.app = SimpleNamespace(
            client_manager=SimpleNamespace(coast=self.api),
            stdout=io.StringIO())
        self.http.get(BASE + 'services', json={'services': [
            {'id': APP, 'name': 'web', 'kind': 'app'},
            {'id': SERVICE, 'name': 'db', 'kind': 'service'}]})

    def execute(self, cls, argv):
        cmd = cls(self.app, None)
        return cmd.take_action(cmd.get_parser('test').parse_args(argv))

    def test_parser_keeps_the_remote_command(self):
        cmd = commands.ExecApp(self.app, None)
        args = cmd.get_parser('test').parse_args(['web', '--', 'ls', '-la'])
        self.assertEqual('web', args.target)
        self.assertEqual(['ls', '-la'], args.command)
        empty = cmd.get_parser('test').parse_args(['web'])
        self.assertEqual([], empty.command)

    def test_posts_console_at_19_without_changing_selection(self):
        self.http.post(BASE + 'apps/' + APP + '/console', status_code=201,
                       json={'url': 'wss://console.example/?token=t',
                             'expires_at': '2026-10-18T00:01:00Z'})
        self.api.microversion = '1.1'
        with mock.patch('coastclient.osc.v1.terminal.terminal.run',
                        return_value=0) as run:
            code = self.execute(commands.ExecApp, ['web'])
        self.assertEqual(0, code)
        self.assertEqual('1.1', self.api.microversion)
        request = self.http.last_request
        self.assertEqual('POST', request.method)
        self.assertEqual(BASE + 'apps/' + APP + '/console', request.url)
        self.assertFalse(request.body)
        self.assertEqual('app-hosting 1.9',
                         request.headers['OpenStack-API-Version'])
        self.assertTrue(run.call_args.kwargs['interactive'])
        self.assertEqual('wss://console.example/?token=t',
                         run.call_args.args[0])

    def test_service_exec_sends_command_and_returns_remote_status(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        ca_crt, _other, crt, key = make_certs(directory)
        stdout = io.BytesIO()

        def handler(conn, rest):
            conn.sendall(server_frame(terminal.OP_BINARY, b'uid=1\n'))
            conn.sendall(server_frame(
                terminal.OP_TEXT, b'{"type":"exit","code":3}'))

        peer = FakePeer(handler, tls=(crt, key))
        self.session.verify = ca_crt
        self.http.post(BASE + 'services/' + SERVICE + '/console',
                       status_code=201, json={
                           'url': peer.url(scheme='wss', token='sekret'),
                           'expires_at': '2026-10-18T00:01:00Z'})
        try:
            with mock.patch('coastclient.osc.v1.terminal.sys.stdout',
                            SimpleNamespace(buffer=stdout)):
                code = self.execute(
                    commands.ExecService, ['db', '--', 'id'])
        finally:
            peer.close()
        if peer.error:
            raise peer.error
        self.assertEqual(3, code)
        self.assertEqual(b'uid=1\n', stdout.getvalue())
        self.assertEqual({'command': ['id']}, self.http.last_request.json())
        self.assertEqual('app-hosting 1.9',
                         self.http.last_request.headers[
                             'OpenStack-API-Version'])
        self.assertIn('token=sekret', peer.request.split('\r\n')[0])
        self.assertNotIn('test-token', peer.request)
        self.assertNotIn('sekret', stdout.getvalue().decode('ascii'))

    def test_names_errors_and_hidden_address(self):
        self.http.post(BASE + 'apps/' + APP + '/console', status_code=406,
                       json={'error': {'message': 'private'}})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, commands.ExecApp, [APP])
        self.assertEqual(
            'A container terminal needs Coast API 1.9 or later.', str(error))
        self.assertNotIn('private', str(error))
        self.http.post(BASE + 'apps/' + APP + '/console', status_code=404,
                       json={'error': {'message': 'private'}})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, commands.ExecApp, [APP])
        self.assertIn('not found.', str(error))
        self.assertNotIn('private', str(error))
        for argv, message in (
                (['missing'], 'App'),
                (['db'], 'App'),
                (['web'], 'App or service')):
            cls = (commands.ExecService if argv == ['web']
                   else commands.ExecApp)
            error = self.assertRaises(
                exceptions.CommandError, self.execute, cls, argv)
            self.assertIn(message, str(error))
        self.http.get(BASE + 'services', json={'services': [
            {'id': APP, 'name': 'web', 'kind': 'app'},
            {'id': SERVICE, 'name': 'web', 'kind': 'app'}]})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, commands.ExecApp, ['web'])
        self.assertIn('More than one app', str(error))
        self.http.post(BASE + 'apps/' + APP + '/console', status_code=201,
                       json={'url': 'ws://127.0.0.1/?token=sekret'})
        error = self.assertRaises(
            exceptions.CommandError, self.execute, commands.ExecApp, [APP])
        self.assertEqual('Invalid terminal address.', str(error))
        self.assertNotIn('sekret', str(error))
        self.session.timeout = 2
        self.http.post(BASE + 'apps/' + APP + '/console', status_code=201,
                       json={'url': 'wss://127.0.0.1:1/?token=sekret'})
        logs = self.useFixture(fixtures.FakeLogger(level=1))
        error = self.assertRaises(
            exceptions.CommandError, self.execute, commands.ExecApp, [APP])
        self.assertEqual('Terminal connection failed.', str(error))
        self.assertNotIn('sekret', str(error))
        self.assertNotIn('sekret', logs.output)
        target = parse.urlsplit(
            'wss://127.0.0.1:1/?token=sekret').query
        self.assertNotIn(target, logs.output)
