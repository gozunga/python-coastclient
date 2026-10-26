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

"""Container terminal session.

The wire protocol is RFC 6455. Client frames are masked. The standard
library provides the sockets and TLS. No third-party WebSocket package
is used. TLS verification follows the OpenStack session CA setting.
https://www.rfc-editor.org/rfc/rfc6455
https://docs.openstack.org/keystoneauth/latest/using-sessions.html
"""
import base64
import fcntl
import hashlib
import json
import os
import select
import signal
import socket
import ssl
import struct
import sys
import termios
import time
import tty
from urllib import parse

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
_ACCEPT_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_MAX_FRAME = 8 * 1024 * 1024
_MAX_TEXT = 65536
_STDIN_CHUNK = 16384
_EXIT_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class TerminalError(Exception):
    """The terminal session could not continue."""


def tls_context(verify):
    """Build a client TLS context from a keystoneauth verify value.

    ``False`` skips verification, as ``--insecure`` does for HTTP.
    A path is a CA bundle. ``True`` or ``None`` uses the default CAs.
    """
    if verify is False:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # nosec B501
        return context
    if verify is True or verify is None:
        return ssl.create_default_context()
    if isinstance(verify, str) and verify:
        return ssl.create_default_context(cafile=verify)
    raise TerminalError('Terminal connection failed.')


def encode_frame(opcode, payload):
    """Return one masked client frame (RFC 6455 section 5.3)."""
    if len(payload) > _MAX_FRAME:
        raise TerminalError('Terminal connection failed.')
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack('!H', length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack('!Q', length))
    key = os.urandom(4)
    header.extend(key)
    masked = bytes(byte ^ key[index % 4]
                   for index, byte in enumerate(payload))
    return bytes(header) + masked


def pop_frame(buf, allow_mask=False):
    """Remove one frame from buf.

    Return ``(fin, opcode, payload)`` or None when more bytes are
    required. Server frames must be unmasked unless allow_mask is set.
    """
    if len(buf) < 2:
        return None
    first, second = buf[0], buf[1]
    if first & 0x70:
        raise TerminalError('Terminal connection failed.')
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length, offset = _frame_length(buf, second & 0x7F)
    if length is None:
        return None
    if masked and not allow_mask:
        raise TerminalError('Terminal connection failed.')
    mask_len = 4 if masked else 0
    total = offset + mask_len + length
    if len(buf) < total:
        return None
    payload = bytes(buf[offset + mask_len:total])
    if masked:
        key = bytes(buf[offset:offset + 4])
        payload = bytes(byte ^ key[index % 4]
                        for index, byte in enumerate(payload))
    del buf[:total]
    if opcode in (OP_CLOSE, OP_PING, OP_PONG) and (not fin or length > 125):
        raise TerminalError('Terminal connection failed.')
    return fin, opcode, payload


def _frame_length(buf, length):
    offset = 2
    if length == 126:
        if len(buf) < 4:
            return None, offset
        length = struct.unpack('!H', buf[2:4])[0]
        offset = 4
    elif length == 127:
        if len(buf) < 10:
            return None, offset
        length = struct.unpack('!Q', buf[2:10])[0]
        offset = 10
        if length > 0x7FFFFFFFFFFFFFFF:
            raise TerminalError('Terminal connection failed.')
    if length > _MAX_FRAME:
        raise TerminalError('Terminal connection failed.')
    return length, offset


def window_size(fd):
    """Return ``(cols, rows)`` for a terminal, or 80x24."""
    try:
        packed = fcntl_winsize(fd)
        rows, cols, _, _ = struct.unpack('HHHH', packed)
    except (OSError, AttributeError):
        return 80, 24
    if rows <= 0 or cols <= 0:
        return 80, 24
    return cols, rows


def fcntl_winsize(fd):
    """Read TIOCGWINSZ. Split out so tests can replace it."""
    return fcntl.ioctl(fd, termios.TIOCGWINSZ,
                       struct.pack('HHHH', 0, 0, 0, 0))


def resize_message(cols, rows):
    """Return the text payload that reports the window size."""
    return json.dumps(
        {'type': 'resize', 'cols': cols, 'rows': rows},
        separators=(',', ':')).encode('ascii')


def connect(url, verify, timeout=None):
    """Open a WebSocket and complete the HTTP upgrade.

    ``ws`` and ``wss`` are accepted here. The command layer allows only
    ``wss`` addresses from the API. The returned socket is non-blocking.
    """
    parts = parse.urlsplit(url)
    if parts.scheme not in ('ws', 'wss') or not parts.hostname:
        raise TerminalError('Invalid terminal address.')
    if parts.username or parts.password or any(
            char in url for char in '\r\n'):
        raise TerminalError('Invalid terminal address.')
    tls = parts.scheme == 'wss'
    port = parts.port or (443 if tls else 80)
    target = parts.path or '/'
    if parts.query:
        target += '?' + parts.query
    host_header = _host_header(parts.hostname, port, tls)
    try:
        raw = socket.create_connection((parts.hostname, port), timeout)
    except OSError:
        raise TerminalError('Terminal connection failed.') from None
    try:
        if tls:
            raw = tls_context(verify).wrap_socket(
                raw, server_hostname=parts.hostname)
        request, key = _opening_handshake(host_header, target)
        raw.sendall(request)
        leftover = _read_upgrade(raw, key)
    except TerminalError:
        raw.close()
        raise
    except (OSError, ssl.SSLError, UnicodeError):
        raw.close()
        raise TerminalError('Terminal connection failed.') from None
    raw.setblocking(False)
    return raw, leftover


def _host_header(host, port, tls):
    name = '[%s]' % host if ':' in host else host
    if port != (443 if tls else 80):
        name = '%s:%s' % (name, port)
    return name


def _opening_handshake(host_header, target):
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    request = (
        'GET %s HTTP/1.1\r\n'
        'Host: %s\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        'Sec-WebSocket-Key: %s\r\n'
        'Sec-WebSocket-Version: 13\r\n'
        '\r\n' % (target, host_header, key)).encode('ascii')
    return request, key


def _read_upgrade(sock, key):
    data = bytearray()
    while b'\r\n\r\n' not in data:
        if len(data) > 8192:
            raise TerminalError('Terminal connection failed.')
        chunk = sock.recv(4096)
        if not chunk:
            raise TerminalError('Terminal connection failed.')
        data += chunk
    head, rest = bytes(data).split(b'\r\n\r\n', 1)
    _validate_upgrade(head.decode('iso-8859-1'), key)
    return rest


def _validate_upgrade(head, key):
    lines = head.split('\r\n')
    status = lines[0].split(' ')
    if len(status) < 2 or status[1] != '101':
        raise TerminalError('Terminal connection failed.')
    headers = {}
    for line in lines[1:]:
        if ':' not in line:
            raise TerminalError('Terminal connection failed.')
        name, value = line.split(':', 1)
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    upgrade = ''.join(headers.get('upgrade', [])).lower()
    connection = ','.join(headers.get('connection', [])).lower()
    if upgrade != 'websocket' or 'upgrade' not in connection:
        raise TerminalError('Terminal connection failed.')
    digest = hashlib.sha1(
        (key + _ACCEPT_GUID).encode('ascii'),
        usedforsecurity=False).digest()
    expected = base64.b64encode(digest).decode('ascii')
    accept = ''.join(headers.get('sec-websocket-accept', []))
    if accept != expected:
        raise TerminalError('Terminal connection failed.')


class TtyGuard:
    """Put a tty in raw mode and restore it on exit and on signals."""

    def __init__(self, fd):
        self.fd = fd
        self.original = None
        self.active = False
        self._previous = {}

    def __enter__(self):
        self.original = termios.tcgetattr(self.fd)
        self._engage()
        for signum in _EXIT_SIGNALS:
            self._previous[signum] = signal.signal(signum, self._on_exit)
        self._previous[signal.SIGTSTP] = signal.signal(
            signal.SIGTSTP, self._on_stop)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.restore()
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)
        self._previous.clear()
        return False

    def _engage(self):
        tty.setraw(self.fd, termios.TCSANOW)
        self.active = True

    def restore(self):
        """Return the tty to the mode saved on entry."""
        if self.original is None or not self.active:
            return
        self.active = False
        try:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
        except OSError:
            pass

    def _on_exit(self, signum, frame):
        self.restore()
        previous = self._previous.get(signum, signal.SIG_DFL)
        signal.signal(signum, previous)
        if previous == signal.SIG_IGN:
            return
        if previous in (signal.SIG_DFL, None) or not callable(previous):
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        previous(signum, frame)

    def _on_stop(self, signum, frame):
        self.restore()
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTSTP)
        signal.signal(signal.SIGTSTP, self._on_stop)
        if self.original is not None:
            self._engage()


class _Link:
    """Non-blocking socket with a byte buffer."""

    def __init__(self, sock, leftover):
        self.sock = sock
        self.incoming = bytearray(leftover)
        self.outgoing = bytearray()
        self.need_write = False
        self.eof = False

    def send(self, opcode, payload):
        self.outgoing += encode_frame(opcode, payload)

    def flush(self):
        self._send()
        while self._recv():
            pass
        pending = getattr(self.sock, 'pending', None)
        while pending is not None and pending() and self._recv():
            pass

    def _send(self):
        if not self.outgoing:
            return
        try:
            count = self.sock.send(self.outgoing)
        except ssl.SSLWantReadError:
            return
        except ssl.SSLWantWriteError:
            self.need_write = True
            return
        except BlockingIOError:
            return
        except OSError:
            raise TerminalError('Terminal connection failed.') from None
        if not count:
            raise TerminalError('Terminal connection failed.')
        del self.outgoing[:count]
        self.need_write = False

    def _recv(self):
        try:
            chunk = self.sock.recv(65536)
        except ssl.SSLWantWriteError:
            self.need_write = True
            return False
        except (BlockingIOError, ssl.SSLWantReadError):
            return False
        except OSError:
            raise TerminalError('Terminal connection failed.') from None
        if chunk == b'':
            self.eof = True
            return False
        self.incoming += chunk
        return True

    def close_politely(self):
        try:
            self.outgoing += encode_frame(OP_CLOSE, struct.pack('!H', 1000))
            deadline = _monotonic() + 0.2
            while self.outgoing and _monotonic() < deadline:
                self._send()
                if self.outgoing:
                    select.select([], [self.sock], [], 0.05)
        except (OSError, TerminalError, ValueError):
            pass


def _monotonic():
    return time.monotonic()


def run(url, verify, interactive=False, stdin=None, stdout=None,
        timeout=None, winsize=None, session_timeout=None):
    """Connect and transfer terminal bytes.

    Interactive mode sends window-size messages and, when stdin is a
    tty, puts it in raw mode. A one-off command only writes output and
    returns the remote status. The return value is that status.
    """
    if stdin is None:
        stdin = sys.stdin.buffer
    if stdout is None:
        stdout = sys.stdout.buffer
    sock = None
    winch_r = winch_w = None
    previous_winch = None
    guard = None
    try:
        sock, leftover = connect(url, verify, timeout)
        link = _Link(sock, leftover)
        stdin_fd = _fileno(stdin) if interactive else None
        if interactive:
            winch_r, winch_w = os.pipe()
            os.set_blocking(winch_r, False)
            previous_winch = signal.signal(
                signal.SIGWINCH, _winch_writer(winch_w))
            if stdin_fd is not None and os.isatty(stdin_fd):
                guard = TtyGuard(stdin_fd)
                guard.__enter__()
            link.send(OP_TEXT, resize_message(
                *_window(winsize, stdin, stdout)))
        return _session(
            link, stdout, stdin_fd if interactive else None, winch_r,
            lambda: _window(winsize, stdin, stdout), session_timeout)
    finally:
        if guard is not None:
            guard.__exit__(None, None, None)
        if previous_winch is not None:
            signal.signal(signal.SIGWINCH, previous_winch)
        for pipe in (winch_r, winch_w):
            if pipe is not None:
                os.close(pipe)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _fileno(stream):
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def _window(winsize, stdin, stdout):
    if winsize is not None:
        cols, rows = winsize() if callable(winsize) else winsize
        return int(cols), int(rows)
    for stream in (stdin, stdout):
        fd = _fileno(stream)
        if fd is not None and os.isatty(fd):
            return window_size(fd)
    return 80, 24


def _winch_writer(write_fd):
    def handler(signum, frame):
        try:
            os.write(write_fd, b'\x01')
        except OSError:
            pass
    return handler


def _session(link, stdout, stdin_fd, winch_r, size, session_timeout):
    fragment = None
    text = bytearray()
    stdin_open = stdin_fd is not None
    while True:
        link.flush()
        code = _drain(link, stdout, fragment, text)
        fragment, text = code[1], code[2]
        if code[0] is not None:
            link.close_politely()
            return code[0]
        if link.eof:
            link.close_politely()
            return 1
        ready = _wait(link, stdin_fd if stdin_open else None, winch_r,
                      session_timeout)
        if ready is None:
            raise TerminalError('Terminal connection failed.')
        readable, writable = ready
        if link.sock in readable or link.sock in writable:
            link.flush()
        if winch_r is not None and winch_r in readable:
            _drain_pipe(winch_r)
            link.send(OP_TEXT, resize_message(*size()))
        if stdin_open and stdin_fd in readable:
            stdin_open = _forward_stdin(link, stdin_fd)


def _drain(link, stdout, fragment, text):
    while True:
        frame = pop_frame(link.incoming)
        if frame is None:
            return None, fragment, text
        fin, opcode, payload = frame
        if opcode == OP_CLOSE:
            return 1, fragment, text
        if opcode == OP_PING:
            link.send(OP_PONG, payload)
            continue
        if opcode == OP_PONG:
            continue
        if opcode in (OP_TEXT, OP_BINARY):
            if fragment is not None:
                raise TerminalError('Terminal connection failed.')
            if not fin:
                fragment = opcode
                if opcode == OP_TEXT:
                    if len(payload) > _MAX_TEXT:
                        raise TerminalError('Terminal connection failed.')
                    text = bytearray(payload)
                else:
                    _write(stdout, payload)
                continue
            if opcode == OP_BINARY:
                _write(stdout, payload)
                continue
            code = _exit_code(payload)
            if code is not None:
                return code, fragment, text
            continue
        if opcode == OP_CONTINUATION:
            if fragment is None:
                raise TerminalError('Terminal connection failed.')
            if fragment == OP_BINARY:
                _write(stdout, payload)
            else:
                text += payload
                if len(text) > _MAX_TEXT:
                    raise TerminalError('Terminal connection failed.')
            if fin:
                opcode = fragment
                fragment = None
                if opcode == OP_TEXT:
                    code = _exit_code(bytes(text))
                    text = bytearray()
                    if code is not None:
                        return code, fragment, text
            continue
        raise TerminalError('Terminal connection failed.')


def _write(stdout, payload):
    try:
        stdout.write(payload)
        stdout.flush()
    except BrokenPipeError:
        raise
    except OSError:
        raise TerminalError('Terminal connection failed.') from None


def _exit_code(payload):
    if len(payload) > _MAX_TEXT:
        raise TerminalError('Terminal connection failed.')
    try:
        message = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(message, dict) or message.get('type') != 'exit':
        return None
    code = message.get('code')
    if type(code) is not int or code < 0 or code > 255:
        return 1
    return code


def _wait(link, stdin_fd, winch_r, timeout):
    reads = [link.sock]
    if stdin_fd is not None:
        reads.append(stdin_fd)
    if winch_r is not None:
        reads.append(winch_r)
    writes = [link.sock] if link.outgoing or link.need_write else []
    try:
        readable, writable, _ = select.select(reads, writes, [], timeout)
    except InterruptedError:
        return [], []
    if timeout is not None and not readable and not writable:
        return None
    return readable, writable


def _drain_pipe(fd):
    while True:
        try:
            if not os.read(fd, 64):
                return
        except (BlockingIOError, OSError):
            return


def _forward_stdin(link, stdin_fd):
    try:
        data = os.read(stdin_fd, _STDIN_CHUNK)
    except BlockingIOError:
        return True
    except OSError:
        raise TerminalError('Terminal connection failed.') from None
    if data == b'':
        return False
    link.send(OP_BINARY, data)
    return True
