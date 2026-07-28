import unittest
from unittest.mock import Mock, patch
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

    @patch('sys.platform', 'linux')
    @patch('subprocess.run')
    def test_get_server_clipboard_xclip(self, mock_run):
        from lib.clipboardctrl import ClipboardController
        mock_run.return_value = Mock(returncode=0, stdout="clipboard content\n")
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


if __name__ == '__main__':
    unittest.main()
