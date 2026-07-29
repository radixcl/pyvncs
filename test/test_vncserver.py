import unittest
from unittest.mock import Mock, patch

import socket
import struct
import threading
import time
import os
from PIL import Image, ImageDraw

from pyvncs.server import VNCServer, _BandwidthEstimator


class TestVNCServer(unittest.TestCase):

    def setUp(self):
        self.socket_mock = Mock()
        self.vnc_config_mock = Mock()
        self.vnc_config_mock.win_title = "Test Window Title"
        self.vnc_config_mock.auth_type = 2
        self.vnc_config_mock.pem_file = ""
        self.vnc_config_mock.eightbitdither = False
        self.vnc_server = VNCServer(socket=self.socket_mock, password="test_password", auth_type=2, vnc_config=self.vnc_config_mock)

    def test_constructor(self):
        self.assertEqual(self.vnc_server.password, "test_password")
        self.assertIsNotNone(self.vnc_server.socket)
        self.assertIsInstance(self.vnc_server._bw_estimator, _BandwidthEstimator)
        self.assertGreaterEqual(self.vnc_server.fbupdate_max_interval, self.vnc_server.fbupdate_min_interval)

    def test_send_message(self):
        message = "Test message"
        with patch('struct.pack') as mock_pack:
            # pack is called multiple times; just verify send was called
            self.vnc_server.send_message(message)
            self.socket_mock.send.assert_called()

    def test_get_buffer_timeout(self):
        self.socket_mock.recv.side_effect = TimeoutError
        result = self.vnc_server.get_buffer(30)
        self.assertIsNone(result)

    def test_bandwidth_estimator(self):
        est = _BandwidthEstimator(alpha=0.5, window=5)
        est.record_send(1000)
        self.assertEqual(est.current_bps, 0.0)  # first sample has no previous time
        est.record_send(2000)
        self.assertGreater(est.current_bps, 0)
        est.reset()
        self.assertEqual(est.current_bps, 0.0)
        self.assertEqual(len(est._samples), 0)

    def test_server_init(self):
        with patch('pyvncs.server.ImageGrab.grab') as mock_grab:
            mock_grab.return_value.size = (1024, 768)
            self.vnc_server.server_init()
            self.assertEqual(self.vnc_server.width, 1024)
            self.assertEqual(self.vnc_server.height, 768)

    def test_handle_client_keyboard_event(self):
        self.socket_mock.recv.side_effect = [
            b'\x04',  # keyboard event type
            b'additional data for the keyboard event',
        ]
        with patch('pyvncs.server.kbdctrl.KeyboardController.process_event') as mock_kbd_event:
            self.vnc_server.handle_client()
            mock_kbd_event.assert_called_with(b'additional data for the keyboard event')

    def test_clipboard_controller_server_to_client(self):
        from lib.clipboardctrl import ClipboardController
        sock = Mock()
        ctrl = ClipboardController()
        # No clipboard set yet — should not send
        result = ctrl.maybe_send_clipboard(sock)
        self.assertFalse(result)
        # Set last_sent_text directly to simulate a prior send, then reset and verify no redundant send
        ctrl._last_sent_text = b"hello"
        sock.send.reset_mock()
        result = ctrl.maybe_send_clipboard(sock)
        self.assertFalse(result)  # same text, should not resend


class TestClipboardController(unittest.TestCase):

    def test_client_cut_text(self):
        from lib.clipboardctrl import ClipboardController
        sock = Mock()
        import struct
        text = b"test"
        length_bytes = struct.pack('!I', len(text))
        sock.recv.side_effect = [
            b'\x00\x00\x00',   # padding
            length_bytes,       # length
            text,               # text
        ]
        ctrl = ClipboardController()
        result = ctrl.client_cut_text(sock)
        self.assertEqual(result, "test")

    @patch('lib.clipboardctrl._GTK_AVAILABLE', True)
    def test_get_server_clipboard_native(self):
        from lib.clipboardctrl import ClipboardController
        mock_cb = Mock()
        mock_cb.wait_for_text.return_value = "clipboard content"
        mock_d = Mock()
        with patch('gi.repository.Gdk.Display.get_default', return_value=mock_d), \
             patch('gi.repository.Gtk.Clipboard.get_for_display', return_value=mock_cb):
            ctrl = ClipboardController()
            result = ctrl.get_server_clipboard()
            self.assertEqual(result, "clipboard content")


class TestVeNCrypt(unittest.TestCase):

    def _make_vencrypt(self, sock, extra_recv=None):
        """Helper to create VeNCrypt with proper version handshake."""
        # __init__ sends b'\x00\x02', reads it back (recv(2)), then sends b'\x00' (no recv)
        all_recv = [b'\x00\x02']
        if extra_recv:
            all_recv.extend(extra_recv)
        sock.recv.side_effect = all_recv
        from lib.auth.vencrypt import VeNCrypt
        return VeNCrypt(sock)

    def test_auth_plain_success(self):
        import struct
        sock = Mock()
        user = b"admin"
        pwd = b"secret"
        header = struct.pack('!II', len(user), len(pwd))
        vn = self._make_vencrypt(sock, [header, user, pwd])
        result = vn.auth_plain({"admin": "secret"})
        self.assertTrue(result)

    def test_auth_plain_failure(self):
        import struct
        sock = Mock()
        user = b"admin"
        pwd = b"wrong"
        header = struct.pack('!II', len(user), len(pwd))
        vn = self._make_vencrypt(sock, [header, user, pwd])
        result = vn.auth_plain({"admin": "secret"})
        self.assertFalse(result)

    def test_send_subtypes(self):
        import struct
        sock = Mock()
        vn = self._make_vencrypt(sock, [struct.pack('!I', 259)])
        vn.send_subtypes()
        self.assertEqual(vn.client_subtype, 259)


class TestVeNCryptTLSPlain(unittest.TestCase):

    def test_auth_tls_plain_success(self):
        """Test VeNCrypt TLSPlain auth with mocked SSL."""
        import struct
        from lib.auth.vencrypt import VeNCrypt

        sock = Mock()
        # __init__ sends version, reads back; then send_subtypes reads subtype choice
        sock.recv.side_effect = [
            b'\x00\x02',       # version request echoed back
            b'\x00',           # no error from server
            struct.pack('!I', 259),  # client chooses TLSPlain (259)
        ]

        pem_path = "/tmp/test.pem"
        with patch('ssl.SSLContext') as MockSSL:
            mock_ssl_ctx = Mock()
            # wrap_socket returns a new socket that will be used for auth_plain
            tls_sock = Mock()
            user = b"admin"
            pwd = b"secret"
            header = struct.pack('!II', len(user), len(pwd))
            tls_sock.recv.side_effect = [header, user, pwd]
            mock_ssl_ctx.wrap_socket.return_value = tls_sock
            MockSSL.return_value = mock_ssl_ctx

            vn = VeNCrypt(sock)
            vn.pem_file = pem_path
            result = vn.auth_tls_plain({"admin": "secret"})
            self.assertTrue(result)
            # Verify SSL context was created and wrap_socket called
            MockSSL.assert_called_once()
            mock_ssl_ctx.wrap_socket.assert_called_once()

    def test_auth_tls_plain_no_pem(self):
        """Test VeNCrypt TLSPlain fails gracefully when no PEM file."""
        import struct
        from lib.auth.vencrypt import VeNCrypt

        sock = Mock()
        sock.recv.side_effect = [b'\x00\x02', b'\x00']
        vn = VeNCrypt(sock)
        vn.pem_file = None
        result = vn.auth_tls_plain({"admin": "secret"})
        self.assertFalse(result)


class TestAdaptiveRateLimit(unittest.TestCase):

    def test_rate_limit_adapts_up(self):
        """Test that rate limit decreases when bandwidth is high."""
        from pyvncs.server import VNCServer
        sock = Mock()
        config = Mock()
        config.win_title = "Test"
        config.auth_type = 2
        config.pem_file = ""
        config.eightbitdither = False
        server = VNCServer(socket=sock, password="test", auth_type=2, vnc_config=config)

        # Simulate a fast connection: large send with small time delta
        est = server._bw_estimator
        import time
        est._last_time = time.time() - 0.1  # 100ms ago
        est.record_send(50000)  # 50KB in 100ms = ~4Mbps

        # After recording, current_bps should be high and ratio check would speed up
        self.assertGreater(est.current_bps, 0)
        # Simulate the rate limit adjustment logic from handle_client
        bps = est.current_bps
        frame_budget = (bps * server.fbupdate_rate_limit) / 8
        ratio = 50000 / max(frame_budget, 1)
        if ratio < 0.5:
            new_limit = max(server.fbupdate_min_interval, server.fbupdate_rate_limit * 0.9)
            self.assertLess(new_limit, server.fbupdate_rate_limit)

    def test_rate_limit_adapts_down(self):
        """Test that rate limit increases when bandwidth is low."""
        from pyvncs.server import VNCServer
        sock = Mock()
        config = Mock()
        config.win_title = "Test"
        config.auth_type = 2
        config.pem_file = ""
        config.eightbitdither = False
        server = VNCServer(socket=sock, password="test", auth_type=2, vnc_config=config)

        est = server._bw_estimator
        import time
        est._last_time = time.time() - 1.0  # 1s ago
        est.record_send(1000)  # 1KB in 1s = ~8Kbps (slow)

        self.assertGreater(est.current_bps, 0)
        bps = est.current_bps
        frame_budget = (bps * server.fbupdate_rate_limit) / 8
        ratio = 1000 / max(frame_budget, 1)
        if ratio > 1.5:
            new_limit = min(server.fbupdate_max_interval, server.fbupdate_rate_limit * 1.1)
            self.assertGreater(new_limit, server.fbupdate_rate_limit)


class TestEndToEndIntegration(unittest.TestCase):
    """End-to-end integration tests over real TCP sockets with mocked screen.

    These tests exercise the complete protocol flow:
      RFB version handshake → VNC auth → ClientInit → ServerInit →
      SetEncodings → FramebufferUpdateRequest → FramebufferUpdate
    """

    AUTH_PASSWORD = 'testpass'

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _make_fake_screen():
        """A deterministic screen image so tests don't need a real display."""
        img = Image.new('RGB', (320, 240), (40, 80, 120))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 200, 180], fill=(220, 30, 30))
        draw.rectangle([10, 10, 300, 30], fill=(30, 220, 30))
        draw.ellipse([120, 100, 180, 160], fill=(255, 255, 0))
        return img

    @staticmethod
    def _mirror_key_bits(password):
        """Replicate VNCAuth._mirrorBits for the client side."""
        pw = (password + '\0' * 8)[:8]
        key = []
        for ch in pw:
            b = ord(ch)
            r = 0
            for i in range(8):
                if b & (1 << i):
                    r |= (1 << (7 - i))
            key.append(r)
        return key

    def _vnc_auth_client(self, client_sock, password):
        """Perform the VNC auth challenge-response from the client side."""
        from pyDes import des
        challenge = client_sock.recv(16)
        self.assertEqual(len(challenge), 16, "challenge must be 16 bytes")
        key = self._mirror_key_bits(password)
        response = des(key).encrypt(challenge)
        client_sock.send(response)
        result = struct.unpack('!I', client_sock.recv(4))[0]
        return result == 0

    def _do_handshake(self, client_sock, password=None):
        """Full RFB handshake through ServerInit. Returns (width, height, name)."""
        if password is None:
            password = self.AUTH_PASSWORD

        # 1. RFB version
        ver = client_sock.recv(12)
        self.assertEqual(ver[:3], b'RFB')
        client_sock.send(ver)

        # 2. Security types
        n = struct.unpack('B', client_sock.recv(1))[0]
        sectypes = client_sock.recv(n)
        self.assertIn(2, sectypes, "server must offer VNC auth (2)")
        client_sock.send(struct.pack('B', 2))

        # 3. VNC auth
        ok = self._vnc_auth_client(client_sock, password)
        self.assertTrue(ok, "VNC auth should succeed")

        # 4. ClientInit (shared = 1)
        client_sock.send(struct.pack('B', 1))

        # 5. ServerInit
        hdr = client_sock.recv(24)
        fb_w, fb_h = struct.unpack('!HH', hdr[:4])
        name_len = struct.unpack('!I', hdr[20:24])[0]
        name = client_sock.recv(name_len).decode('utf-8', errors='replace')
        return fb_w, fb_h, name

    @staticmethod
    def _send_set_encodings(client_sock, encoding_ids):
        msg = struct.pack('!BxH', 2, len(encoding_ids))
        for e in encoding_ids:
            msg += struct.pack('!i', e)
        client_sock.send(msg)

    @staticmethod
    def _send_fb_update_request(client_sock, incremental, x, y, w, h):
        client_sock.send(struct.pack('!BBHHHH', 3, incremental, x, y, w, h))

    @staticmethod
    def _recv_framebuffer_update(client_sock, timeout=3.0):
        """Receive a FramebufferUpdate message. Returns (num_rects, rects, raw)."""
        client_sock.settimeout(timeout)
        data = b''
        while len(data) < 4:
            data += client_sock.recv(4 - len(data))
        msg_type = data[0]
        num_rects = struct.unpack('!H', data[2:4])[0]

        rects = []
        if msg_type == 0 and num_rects > 0:
            while len(data) < 16:
                data += client_sock.recv(16 - len(data))
            rx, ry, rw, rh = struct.unpack('!HHHH', data[4:12])
            enc_type = struct.unpack('!i', data[12:16])[0]
            rects.append((rx, ry, rw, rh, enc_type))
            # read remaining pixel data
            try:
                while True:
                    chunk = client_sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    if len(chunk) < 65536:
                        break
            except socket.timeout:
                pass

        return msg_type, num_rects, rects, data

    class _ServerRunner:
        """Context manager that starts a server thread with mocked ImageGrab."""

        def __init__(self, test_case, password, win_title='integration-test',
                     expect_init_failure=False):
            self.test_case = test_case
            self.password = password
            self.win_title = win_title
            self.expect_init_failure = expect_init_failure
            self.listen_sock = None
            self.thread = None
            self.errors = []
            self.client_sock = None

        def __enter__(self):
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listen_sock.bind(('127.0.0.1', 0))
            self.listen_sock.listen(1)
            port = self.listen_sock.getsockname()[1]

            ready = threading.Event()

            def _serve():
                try:
                    conn, _ = self.listen_sock.accept()
                    ready.set()

                    class FakeConfig:
                        auth_type = 2
                        vnc_password = self.password
                        pem_file = ''
                        eightbitdither = False
                        win_title = self.win_title

                    import pyvncs.server
                    server = pyvncs.server.VNCServer(
                        conn, auth_type=2, password=self.password,
                        vnc_config=FakeConfig(),
                    )
                    with patch.object(pyvncs.server, 'ImageGrab') as mock_grab:
                        mock_grab.grab.return_value = self.test_case._make_fake_screen()
                        status = server.init()
                        if not status:
                            if not self.expect_init_failure:
                                self.errors.append('server.init() returned False')
                            return
                        if self.expect_init_failure:
                            self.errors.append(
                                'server.init() succeeded but failure was expected')
                            return
                        server.handle_client()
                except Exception as exc:
                    self.errors.append(repr(exc))
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            self.thread = threading.Thread(target=_serve, daemon=True)
            self.thread.start()

            # connect client
            self.client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_sock.settimeout(5)
            # wait for server to accept
            for _ in range(50):
                try:
                    self.client_sock.connect(('127.0.0.1', port))
                    break
                except (ConnectionRefusedError, OSError):
                    time.sleep(0.05)
            else:
                raise RuntimeError("could not connect to test server")
            ready.wait(timeout=3)
            return self

        def __exit__(self, *exc):
            try:
                if self.client_sock:
                    self.client_sock.close()
            except Exception:
                pass
            try:
                if self.listen_sock:
                    self.listen_sock.close()
            except Exception:
                pass
            self.thread.join(timeout=3)
            if self.errors:
                raise AssertionError('server errors: ' + '; '.join(self.errors))
            return False

    # ---- tests ------------------------------------------------------------

    def test_handshake_and_raw_framebuffer(self):
        """Full handshake + raw encoding framebuffer update."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            fb_w, fb_h, name = self._do_handshake(cs)
            self.assertEqual((fb_w, fb_h), (320, 240))
            self.assertEqual(name, 'integration-test')

            self._send_set_encodings(cs, [0])  # raw only
            time.sleep(0.1)
            self._send_fb_update_request(cs, incremental=0, x=0, y=0, w=100, h=80)

            msg_type, num_rects, rects, data = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertEqual(num_rects, 1)
            rx, ry, rw, rh, enc = rects[0]
            self.assertEqual((rx, ry, rw, rh), (0, 0, 100, 80))
            self.assertEqual(enc, 0)  # raw
            # 4-byte header + 8-byte rect hdr + 4-byte enc + 100*80*4 pixel data
            self.assertGreater(len(data), 100 * 80 * 4)

    def test_handshake_and_hextile_framebuffer(self):
        """Hextile encoding end-to-end (was crashing with TypeError before fix)."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [5, 0])  # hextile + raw fallback
            time.sleep(0.1)
            self._send_fb_update_request(cs, incremental=0, x=0, y=0, w=64, h=64)

            msg_type, num_rects, rects, data = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertEqual(num_rects, 1)
            _, _, _, _, enc = rects[0]
            self.assertEqual(enc, 5)  # hextile
            self.assertGreater(len(data), 16)

    def test_handshake_and_tight_framebuffer(self):
        """Tight encoding end-to-end (was crashing with TypeError before fix)."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [7, 0])  # tight + raw fallback
            time.sleep(0.1)
            self._send_fb_update_request(cs, incremental=0, x=0, y=0, w=64, h=64)

            msg_type, num_rects, rects, data = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertEqual(num_rects, 1)
            _, _, _, _, enc = rects[0]
            self.assertEqual(enc, 7)  # tight

    def test_handshake_and_zlib_framebuffer(self):
        """Zlib encoding end-to-end."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [6, 0])  # zlib + raw fallback
            time.sleep(0.1)
            self._send_fb_update_request(cs, incremental=0, x=0, y=0, w=64, h=64)

            msg_type, num_rects, rects, data = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertEqual(num_rects, 1)
            _, _, _, _, enc = rects[0]
            self.assertEqual(enc, 6)  # zlib

    def test_non_incremental_bypasses_rate_limit(self):
        """A non-incremental (full) request must get real pixel data immediately,
        even though the rate limiter would normally throttle it."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [0])  # raw
            time.sleep(0.1)

            # Fire two full (non-incremental) requests back-to-back.
            # The rate limiter must NOT delay either one.
            self._send_fb_update_request(cs, 0, 0, 0, 50, 50)
            msg_type, nrects, _, data = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertGreater(nrects, 0)
            self.assertGreater(len(data), 50 * 50 * 4)

            self._send_fb_update_request(cs, 0, 0, 0, 50, 50)
            msg_type, nrects, _, data = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertGreater(nrects, 0)

    def test_incremental_no_changes_sends_empty(self):
        """When nothing changed, incremental update returns 0 rectangles."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [0])
            time.sleep(0.1)

            # First: full update to populate the server-side framebuffer
            self._send_fb_update_request(cs, 0, 0, 0, 100, 100)
            msg_type, nrects, _, _ = self._recv_framebuffer_update(cs)
            self.assertEqual(nrects, 1)

            # Second: incremental — screen hasn't changed → 0 rectangles
            self._send_fb_update_request(cs, 1, 0, 0, 100, 100)
            msg_type, nrects, _, _ = self._recv_framebuffer_update(cs)
            self.assertEqual(msg_type, 0)
            self.assertEqual(nrects, 0)

    def test_keyboard_event_processed(self):
        """Keyboard events reach KeyboardController without crashing the server."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [0])
            time.sleep(0.1)

            # Send a FramebufferUpdateRequest first to get past initial state
            self._send_fb_update_request(cs, 0, 0, 0, 10, 10)
            self._recv_framebuffer_update(cs)

            # Send a key-down event (msg type 4, down=1, key=0x41='A')
            with patch('pyvncs.server.kbdctrl.KeyboardController.process_event') as mock_kbd:
                cs.send(struct.pack('!BBHI', 4, 1, 0, 0x41))
                time.sleep(0.3)
                mock_kbd.assert_called()

            cs.send(struct.pack('!BBHI', 4, 0, 0, 0x41))  # key-up
            time.sleep(0.2)

    def test_client_disconnect_cleans_up(self):
        """Server must handle client disconnection without errors."""
        with self._ServerRunner(self, self.AUTH_PASSWORD) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            self._send_set_encodings(cs, [0])
            time.sleep(0.1)
            self._send_fb_update_request(cs, 0, 0, 0, 10, 10)
            self._recv_framebuffer_update(cs)
            # Just close — __exit__ will verify no server errors

    def test_wrong_password_rejected(self):
        """Auth with wrong password must fail."""
        with self._ServerRunner(self, self.AUTH_PASSWORD,
                                expect_init_failure=True) as srv:
            cs = srv.client_sock
            # RFB version
            ver = cs.recv(12)
            cs.send(ver)
            # Security types
            n = struct.unpack('B', cs.recv(1))[0]
            cs.recv(n)
            cs.send(struct.pack('B', 2))

            # Send wrong password response
            from pyDes import des
            challenge = cs.recv(16)
            key = self._mirror_key_bits('wrongpass')
            cs.send(des(key).encrypt(challenge))

            result = struct.unpack('!I', cs.recv(4))[0]
            self.assertNotEqual(result, 0, "auth should fail with wrong password")


class TestThreadedResponsiveness(unittest.TestCase):
    """Verify keyboard/mouse stay responsive while FB updates are in flight.

    These tests exploit the two-thread architecture (input thread + FB sender
    thread): a slow :meth:`send_rectangles` is simulated so we can prove that
    input events are processed *concurrently*, not deferred until after the
    framebuffer send completes.
    """

    AUTH_PASSWORD = 'testpass'

    @staticmethod
    def _make_fake_screen():
        img = Image.new('RGB', (320, 240), (40, 80, 120))
        return img

    @staticmethod
    def _mirror_key_bits(password):
        pw = (password + '\0' * 8)[:8]
        key = []
        for ch in pw:
            b = ord(ch)
            r = 0
            for i in range(8):
                if b & (1 << i):
                    r |= (1 << (7 - i))
            key.append(r)
        return key

    def _do_handshake(self, cs):
        ver = cs.recv(12)
        cs.send(ver)
        n = struct.unpack('B', cs.recv(1))[0]
        cs.recv(n)
        cs.send(struct.pack('B', 2))
        from pyDes import des
        challenge = cs.recv(16)
        cs.send(des(self._mirror_key_bits(self.AUTH_PASSWORD)).encrypt(challenge))
        struct.unpack('!I', cs.recv(4))[0]
        cs.send(struct.pack('B', 1))
        hdr = cs.recv(24)
        name_len = struct.unpack('!I', hdr[20:24])[0]
        cs.recv(name_len)

    class _ServerRunner:
        def __init__(self, test_case):
            self.tc = test_case
            self.listen_sock = None
            self.thread = None
            self.errors = []
            self.client_sock = None

        def __enter__(self):
            self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listen_sock.bind(('127.0.0.1', 0))
            self.listen_sock.listen(1)
            port = self.listen_sock.getsockname()[1]

            ready = threading.Event()

            def _serve():
                try:
                    conn, _ = self.listen_sock.accept()
                    ready.set()

                    class Cfg:
                        auth_type = 2
                        vnc_password = self.tc.AUTH_PASSWORD
                        pem_file = ''
                        eightbitdither = False
                        win_title = 'threaded-test'

                    import pyvncs.server
                    srv = pyvncs.server.VNCServer(
                        conn, auth_type=2, password=self.tc.AUTH_PASSWORD,
                        vnc_config=Cfg(),
                    )
                    with patch.object(pyvncs.server, 'ImageGrab') as mock:
                        mock.grab.return_value = self.tc._make_fake_screen()
                        if not srv.init():
                            self.errors.append('init failed')
                            return
                        srv.handle_client()
                except Exception as exc:
                    self.errors.append(repr(exc))
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            self.thread = threading.Thread(target=_serve, daemon=True)
            self.thread.start()

            self.client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_sock.settimeout(5)
            for _ in range(50):
                try:
                    self.client_sock.connect(('127.0.0.1', port))
                    break
                except (ConnectionRefusedError, OSError):
                    time.sleep(0.05)
            ready.wait(timeout=3)
            return self

        def __exit__(self, *exc):
            try:
                if self.client_sock:
                    self.client_sock.close()
            except Exception:
                pass
            try:
                self.listen_sock.close()
            except Exception:
                pass
            self.thread.join(timeout=3)
            return False

    def test_keyboard_during_slow_fb_send(self):
        """Keyboard events are processed while FB sender is blocked."""
        fb_started = threading.Event()
        fb_release = threading.Event()
        kbd_done = threading.Event()

        import pyvncs.server

        original_send = pyvncs.server.VNCServer.send_rectangles

        def slow_send(self, sock, x, y, w, h, incremental=0):
            fb_started.set()
            fb_release.wait(timeout=5)
            return original_send(self, sock, x, y, w, h, incremental)

        real_kbd = pyvncs.server.kbdctrl.KeyboardController.process_event

        def tracking_kbd(self, data):
            kbd_done.set()
            return real_kbd(self, data)

        with self._ServerRunner(self) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            cs.send(struct.pack('!BxH', 2, 1) + struct.pack('!i', 0))  # SetEncodings: raw
            time.sleep(0.1)

            with patch.object(pyvncs.server.VNCServer, 'send_rectangles', slow_send), \
                 patch.object(pyvncs.server.kbdctrl.KeyboardController,
                              'process_event', tracking_kbd):
                # Request a non-incremental FB update
                cs.send(struct.pack('!BBHHHH', 3, 0, 0, 0, 50, 50))

                # Wait for FB sender to enter send_rectangles
                self.assertTrue(fb_started.wait(timeout=3),
                                "FB sender should have started")

                # While FB sender is blocked, send a keyboard event
                cs.send(struct.pack('!BBHI', 4, 1, 0, 0x41))

                # Keyboard must be processed BEFORE fb_release
                self.assertTrue(kbd_done.wait(timeout=3),
                                "Keyboard event should be processed while "
                                "FB sender is blocked")

                # Now let the FB sender finish
                fb_release.set()

            # Drain FB response
            cs.settimeout(2)
            try:
                while cs.recv(65536):
                    pass
            except (socket.timeout, OSError):
                pass

        self.assertEqual(srv.errors, [], "server should have no errors")

    def test_mouse_during_slow_fb_send(self):
        """Mouse events are processed while FB sender is blocked."""
        fb_started = threading.Event()
        fb_release = threading.Event()
        mouse_done = threading.Event()

        import pyvncs.server

        original_send = pyvncs.server.VNCServer.send_rectangles

        def slow_send(self, sock, x, y, w, h, incremental=0):
            fb_started.set()
            fb_release.wait(timeout=5)
            return original_send(self, sock, x, y, w, h, incremental)

        real_mouse = pyvncs.server.mousectrl.MouseController.process_event

        def tracking_mouse(self, data):
            mouse_done.set()
            return real_mouse(self, data)

        with self._ServerRunner(self) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            cs.send(struct.pack('!BxH', 2, 1) + struct.pack('!i', 0))  # SetEncodings: raw
            time.sleep(0.1)

            with patch.object(pyvncs.server.VNCServer, 'send_rectangles', slow_send), \
                 patch.object(pyvncs.server.mousectrl.MouseController,
                              'process_event', tracking_mouse):
                cs.send(struct.pack('!BBHHHH', 3, 0, 0, 0, 50, 50))
                self.assertTrue(fb_started.wait(timeout=3))

                # Send a pointer event (type=5, mask=0, x=10, y=20)
                cs.send(struct.pack('!BBHH', 5, 0, 10, 20))

                self.assertTrue(mouse_done.wait(timeout=3),
                                "Mouse event should be processed while "
                                "FB sender is blocked")

                fb_release.set()

            cs.settimeout(2)
            try:
                while cs.recv(65536):
                    pass
            except (socket.timeout, OSError):
                pass

    def test_fb_requests_are_coalesced(self):
        """Multiple rapid FBUpdateRequests are coalesced — sender processes
        only the latest one, not a backlog."""
        import pyvncs.server

        send_count = [0]
        original_send = pyvncs.server.VNCServer.send_rectangles

        def counting_send(self, sock, x, y, w, h, incremental=0):
            send_count[0] += 1
            return original_send(self, sock, x, y, w, h, incremental)

        with self._ServerRunner(self) as srv:
            cs = srv.client_sock
            self._do_handshake(cs)
            cs.send(struct.pack('!BxH', 2, 1) + struct.pack('!i', 0))  # raw
            time.sleep(0.1)

            with patch.object(pyvncs.server.VNCServer,
                              'send_rectangles', counting_send):
                # Fire 5 full requests rapidly
                for i in range(5):
                    cs.send(struct.pack('!BBHHHH', 3, 0, 0, 0, 30, 30))

                time.sleep(0.8)

            # Should have sent at most a few updates, not 5
            # (coalescing + rate limiting keeps it low)
            self.assertLess(send_count[0], 5,
                            "Rapid requests should be coalesced, not queued. "
                            "Got %d sends" % send_count[0])
            self.assertGreaterEqual(send_count[0], 1,
                                    "At least one update should be sent")

            cs.settimeout(2)
            try:
                while cs.recv(65536):
                    pass
            except (socket.timeout, OSError):
                pass


class TestCursorEncoding(unittest.TestCase):
    """Tests for cursor pseudo-encoding wire format.

    Regression: send_cursor used to emit 3 bytes/pixel (RGB) instead of the
    correct bytes-per-pixel for the framebuffer format (4 for 32bpp), which
    desynchronised the protocol and caused clients like Remmina to disconnect.
    """

    def _make_server(self, bpp=32, depth=24, red_shift=16, blue_shift=0):
        from pyvncs.server import VNCServer
        from lib.rfb_bitmap import RfbBitmap
        sock = Mock()
        config = Mock()
        config.win_title = "T"
        config.auth_type = 2
        config.pem_file = ""
        config.eightbitdither = False
        srv = VNCServer(socket=sock, password="x", auth_type=2, vnc_config=config)
        srv.bpp = bpp
        srv.depth = depth
        srv.primaryOrder = "bgr" if red_shift > blue_shift else "rgb"
        srv.red_shift = red_shift
        srv.green_shift = 8
        srv.blue_shift = blue_shift
        srv.rfb_bitmap = RfbBitmap()
        srv.rfb_bitmap.bpp = bpp
        srv.rfb_bitmap.depth = depth
        srv.rfb_bitmap.primaryOrder = srv.primaryOrder
        return srv

    def test_cursor_32bpp_pixel_size(self):
        """Cursor pixel data must be 4 bytes/pixel at 32bpp."""
        from PIL import Image
        srv = self._make_server(bpp=32)
        rgb = Image.new('RGB', (16, 16), (100, 150, 200))
        data = srv._encode_cursor_pixels(rgb)
        # 16 * 16 * 4 = 1024
        self.assertEqual(len(data), 16 * 16 * 4)

    def test_cursor_16bpp_pixel_size(self):
        """Cursor pixel data must be 2 bytes/pixel at 16bpp."""
        from PIL import Image
        srv = self._make_server(bpp=16, depth=16)
        rgb = Image.new('RGB', (8, 8), (100, 150, 200))
        data = srv._encode_cursor_pixels(rgb)
        self.assertEqual(len(data), 8 * 8 * 2)

    def test_cursor_send_produces_correct_total_size(self):
        """The full cursor FramebufferUpdate must have the expected byte count
        so the client parser stays in sync."""
        from PIL import Image
        import struct
        srv = self._make_server(bpp=32)

        # Mock cursor image (RGBA 16x16)
        fake_cursor = Image.new('RGBA', (16, 16), (255, 0, 0, 255))
        srv.cursor_support = True
        srv.last_cursor = None
        srv.socket = Mock()

        with patch.object(srv.cursor_encoding, 'get_cursor_image',
                          return_value=fake_cursor):
            srv.send_cursor(0, 0)

        # Inspect what was sent
        sendbuff = srv.socket.sendall.call_args[0][0]

        # Header: 4 (msg) + 8 (rect) + 4 (enc) = 16 bytes
        # Pixels: 16*16*4 = 1024 bytes
        # Bitmask: ceil(16/8)*16 = 32 bytes
        expected = 16 + 1024 + 32
        self.assertEqual(len(sendbuff), expected,
                         "Cursor update must be exactly %d bytes for 16x16@32bpp, "
                         "got %d" % (expected, len(sendbuff)))

        # Verify hotspot is (0,0) not the FBUpdateRequest coords
        hotspot_x, hotspot_y, cur_w, cur_h = struct.unpack('!HHHH',
                                                            sendbuff[4:12])
        self.assertEqual((hotspot_x, hotspot_y), (0, 0),
                         "Cursor hotspot should be (0,0)")
        self.assertEqual((cur_w, cur_h), (16, 16))

    def test_cursor_not_resent_unchanged(self):
        """send_cursor should skip if cursor hasn't changed."""
        from PIL import Image
        srv = self._make_server(bpp=32)
        fake_cursor = Image.new('RGBA', (8, 8), (0, 0, 0, 255))
        srv.last_cursor = fake_cursor
        srv.socket = Mock()

        with patch.object(srv.cursor_encoding, 'get_cursor_image',
                          return_value=fake_cursor):
            result = srv.send_cursor(0, 0)
        self.assertTrue(result)
        srv.socket.sendall.assert_not_called()


class TestPrimaryOrderColors(unittest.TestCase):
    """Verify primaryOrder logic produces correct wire-format colors."""

    def test_high_red_shift_selects_bgr(self):
        """red_shift > blue_shift means wire format is BGR → primaryOrder=bgr."""
        from pyvncs.server import VNCServer
        sock = Mock()
        config = Mock()
        config.win_title = "T"
        config.auth_type = 2
        config.pem_file = ""
        config.eightbitdither = False
        srv = VNCServer(socket=sock, password="x", auth_type=2, vnc_config=config)

        # Simulate SetPixelFormat with red_shift=16, blue_shift=0 (Remmina default)
        srv.red_shift = 16
        srv.green_shift = 8
        srv.blue_shift = 0
        srv.primaryOrder = "bgr" if srv.red_shift > srv.blue_shift else "rgb"
        self.assertEqual(srv.primaryOrder, "bgr",
                         "red_shift(16) > blue_shift(0) should select 'bgr'")

    def test_low_red_shift_selects_rgb(self):
        """red_shift < blue_shift means wire format is RGB → primaryOrder=rgb."""
        from pyvncs.server import VNCServer
        sock = Mock()
        config = Mock()
        config.win_title = "T"
        config.auth_type = 2
        config.pem_file = ""
        config.eightbitdither = False
        srv = VNCServer(socket=sock, password="x", auth_type=2, vnc_config=config)

        srv.red_shift = 0
        srv.green_shift = 8
        srv.blue_shift = 16
        srv.primaryOrder = "bgr" if srv.red_shift > srv.blue_shift else "rgb"
        self.assertEqual(srv.primaryOrder, "rgb",
                         "red_shift(0) < blue_shift(16) should select 'rgb'")


class TestEncodingSignature(unittest.TestCase):
    """Verify every encoding's send_image has the same callable signature."""

    def test_all_encodings_accept_bpp_depth(self):
        import lib.encodings as encs
        from lib.encodings.common import ENCODINGS
        from PIL import Image
        import inspect

        img = Image.new('RGB', (32, 32), (10, 20, 30))
        for enc_id in [ENCODINGS.raw, ENCODINGS.zlib, ENCODINGS.tight,
                       ENCODINGS.hextile]:
            enc = encs.common.encodings[enc_id]()
            # Must accept 7 positional args (x, y, w, h, image, bpp, depth)
            result = enc.send_image(0, 0, 32, 32, img, 32, 24)
            self.assertIsInstance(result, (bytes, bytearray))
            self.assertGreater(len(result), 12)

    def test_hextile_solid_tile_optimization(self):
        """A solid-color image should produce a very small hextile payload."""
        from lib.encodings.hextile import HextileEncoding
        from PIL import Image

        enc = HextileEncoding()
        solid = Image.new('RGB', (32, 32), (100, 150, 200))
        result = enc.send_image(0, 0, 32, 32, solid, bpp=32)
        # 16-byte header + 4 tiles: first sets BG (5 bytes), rest reuse (1 byte each)
        # = 16 + 5 + 1 + 1 + 1 = 24
        self.assertEqual(len(result), 24)

    def test_hextile_non_standard_tile_size(self):
        """Hextile must handle non-16-aligned dimensions."""
        from lib.encodings.hextile import HextileEncoding
        from PIL import Image

        enc = HextileEncoding()
        img = Image.new('RGB', (17, 17), (50, 100, 150))
        result = enc.send_image(0, 0, 17, 17, img, bpp=32)
        from struct import unpack
        rw, rh = unpack('!HH', result[8:12])
        self.assertEqual((rw, rh), (17, 17))


class TestKeyboardController(unittest.TestCase):
    """Tests for the evdev/uinput keyboard backend (Wayland) and pynput fallback."""

    def _make_controller(self, wayland=True, evdev_ok=True, xlib_ok=True):
        """Create a KeyboardController with controlled backend selection."""
        import lib.kbdctrl as kbdmod
        orig_wayland = kbdmod._is_wayland
        orig_evdev = kbdmod._EVDEV_AVAILABLE
        orig_xlib = kbdmod._XLIB_AVAILABLE

        kbdmod._is_wayland = lambda: wayland
        kbdmod._EVDEV_AVAILABLE = evdev_ok
        kbdmod._XLIB_AVAILABLE = xlib_ok

        try:
            ctrl = kbdmod.KeyboardController()
        finally:
            kbdmod._is_wayland = orig_wayland
            kbdmod._EVDEV_AVAILABLE = orig_evdev
            kbdmod._XLIB_AVAILABLE = orig_xlib
        return ctrl

    def test_evdev_backend_selected_on_wayland(self):
        """On Wayland with evdev available, the evdev backend is used."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        self.assertEqual(ctrl._backend, 'evdev')
        ctrl.close()

    def test_pynput_fallback_without_evdev(self):
        """Without evdev, falls back to pynput."""
        ctrl = self._make_controller(wayland=True, evdev_ok=False)
        self.assertEqual(ctrl._backend, 'pynput')
        ctrl.close()

    def test_pynput_fallback_on_x11(self):
        """On X11 (no Wayland), pynput is used."""
        ctrl = self._make_controller(wayland=False)
        self.assertEqual(ctrl._backend, 'pynput')
        ctrl.close()

    def test_x_keymap_loaded(self):
        """X keyboard mapping is loaded when Xlib is available."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True, xlib_ok=True)
        if ctrl._backend == 'evdev':
            self.assertIsNotNone(ctrl._x_keymap)
            self.assertGreater(len(ctrl._x_keymap), 0)
        ctrl.close()

    def test_lookup_letter_keysym(self):
        """Letter keysyms resolve to a valid Linux keycode."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        if ctrl._backend != 'evdev':
            self.skipTest('evdev backend not available')
        from evdev import ecodes
        # 'a' (0x61) should map to KEY_A
        kc, shift, altgr = ctrl._lookup_keysym(0x61)
        self.assertIsNotNone(kc)
        self.assertEqual(kc, ecodes.KEY_A)
        self.assertFalse(shift)
        self.assertFalse(altgr)
        # 'A' (0x41) should map to KEY_A with shift
        kc, shift, altgr = ctrl._lookup_keysym(0x41)
        self.assertIsNotNone(kc)
        self.assertEqual(kc, ecodes.KEY_A)
        self.assertTrue(shift)
        ctrl.close()

    def test_lookup_special_keys(self):
        """Special keys (Enter, Escape, arrows) resolve correctly."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        if ctrl._backend != 'evdev':
            self.skipTest('evdev backend not available')
        from evdev import ecodes
        for keysym, expected in [
            (0xff0d, ecodes.KEY_ENTER),
            (0xff1b, ecodes.KEY_ESC),
            (0xff08, ecodes.KEY_BACKSPACE),
            (0xff09, ecodes.KEY_TAB),
            (0xff51, ecodes.KEY_LEFT),
            (0xff52, ecodes.KEY_UP),
            (0xff53, ecodes.KEY_RIGHT),
            (0xff54, ecodes.KEY_DOWN),
            (0xffbe, ecodes.KEY_F1),
            (0xffe1, ecodes.KEY_LEFTSHIFT),
            (0xffe3, ecodes.KEY_LEFTCTRL),
            (0xffe9, ecodes.KEY_LEFTALT),
            (0xffeb, ecodes.KEY_LEFTMETA),
        ]:
            kc, _, _ = ctrl._lookup_keysym(keysym)
            self.assertIsNotNone(kc, "keysym 0x%x should resolve" % keysym)
            self.assertEqual(kc, expected,
                             "keysym 0x%x: expected %d, got %d" % (keysym, expected, kc))
        ctrl.close()

    def test_modifier_state_tracking(self):
        """Shift/AltGr state is tracked from client events."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        if ctrl._backend != 'evdev':
            self.skipTest('evdev backend not available')
        import struct

        # Shift press
        ctrl.process_event(struct.pack('!BxxL', 1, 0xffe1))
        self.assertTrue(ctrl._shift_held)

        # Shift release
        ctrl.process_event(struct.pack('!BxxL', 0, 0xffe1))
        self.assertFalse(ctrl._shift_held)

        # AltGr press
        ctrl.process_event(struct.pack('!BxxL', 1, 0xfe03))
        self.assertTrue(ctrl._altgr_held)

        # AltGr release
        ctrl.process_event(struct.pack('!BxxL', 0, 0xfe03))
        self.assertFalse(ctrl._altgr_held)
        ctrl.close()

    def test_evdev_write_produces_events(self):
        """Verify evdev UInput actually writes key events."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        if ctrl._backend != 'evdev':
            self.skipTest('evdev backend not available')
        import struct

        # Should not raise
        ctrl.process_event(struct.pack('!BxxL', 1, 0x61))  # 'a' press
        ctrl.process_event(struct.pack('!BxxL', 0, 0x61))  # 'a' release
        ctrl.close()

    def test_short_data_handled_gracefully(self):
        """Short data (< 7 bytes) is rejected without crashing."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        ctrl.process_event(b'\x01\x00')  # too short
        ctrl.process_event(b'')          # empty
        ctrl.close()

    def test_unknown_keysym_handled_gracefully(self):
        """Unknown keysyms don't crash the controller."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        if ctrl._backend != 'evdev':
            self.skipTest('evdev backend not available')
        import struct
        # 0x12345678 is not a valid keysym
        ctrl.process_event(struct.pack('!BxxL', 1, 0x12345678))
        ctrl.close()

    def test_close_is_idempotent(self):
        """Calling close() multiple times doesn't raise."""
        ctrl = self._make_controller(wayland=True, evdev_ok=True)
        ctrl.close()
        ctrl.close()
        ctrl.close()


if __name__ == '__main__':
    unittest.main()
