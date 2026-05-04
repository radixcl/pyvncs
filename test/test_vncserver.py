import unittest
from unittest.mock import Mock, patch, MagicMock, call
from pyvncs.server import VNCServer
from lib.auth.vencrypt import VeNCrypt

class TestVNCServer(unittest.TestCase):

    def setUp(self):
        self.socket_mock = Mock()
        self.vnc_config_mock = Mock()
        self.vnc_config_mock.win_title = "Test Window Title"
        self.vnc_config_mock.eightbitdither = False
        self.vnc_server = VNCServer(socket=self.socket_mock, password="test_password", auth_type=2, vnc_config=self.vnc_config_mock)

    def test_constructor(self):
        self.assertEqual(self.vnc_server.password, "test_password")
        self.assertIsNotNone(self.vnc_server.socket)

    def test_send_message(self):
        return True

    def test_get_buffer_timeout(self):
        self.socket_mock.recv.side_effect = TimeoutError
        result = self.vnc_server.get_buffer(30)
        self.assertIsNone(result)

    def test_init(self):
        return True

    def test_server_init(self):
        with patch('pyvncs.server.ImageGrab.grab') as mock_grab:
            mock_grab.return_value.size = (1024, 768)
            self.vnc_server.server_init()
            self.assertEqual(self.vnc_server.width, 1024)
            self.assertEqual(self.vnc_server.height, 768)

    def test_handle_client_keyboard_event(self):
        self.socket_mock.recv.side_effect = [
            b'\x04',
            b'additional data for the keyboard event'
        ]
        with patch('pyvncs.server.kbdctrl.KeyboardController.process_event') as mock_kbd_event:
            self.vnc_server.handle_client()
            mock_kbd_event.assert_called_with(b'additional data for the keyboard event')


class TestVeNCrypt(unittest.TestCase):

    def _create_veencrypt(self, client_version=b'\x00\x02'):
        """Helper to create VeNCrypt with proper version negotiation mock."""
        sock_mock = Mock()
        # VeNCrypt.__init__ does: send(version), recv(2), send(b'\x00')
        sock_mock.recv.return_value = client_version
        sock_mock.send = Mock()
        ve = VeNCrypt(sock_mock)
        ve._sock = sock_mock  # store for later use
        return ve, sock_mock

    def test_init_version_negotiation(self):
        ve, sock = self._create_veencrypt()
        self.assertTrue(sock.send.called)

    def test_subtypes_constants(self):
        self.assertEqual(VeNCrypt.SUBTYPE_PLAIN, 256)
        self.assertEqual(VeNCrypt.SUBTYPE_TLSPLAIN, 259)

    def test_send_subtypes(self):
        ve, sock = self._create_veencrypt()
        sock.recv.reset_mock()
        sock.recv.return_value = b'\x00\x00\x01\x00'  # subtype 256
        
        ve.send_subtypes()
        self.assertEqual(ve.client_subtype, 256)

    def test_auth_plain_success(self):
        ve, sock = self._create_veencrypt()
        sock.send.reset_mock()
        
        # auth_plain calls: recv(8), recv(4), recv(4)
        # Using side_effect so each recv call gets the next value
        sock.recv.side_effect = [
            b'\x00\x00\x00\x04\x00\x00\x00\x04',  # header: 4 byte user, 4 byte pass
            b'user',                               # username
            b'pass',                               # password
        ]
        
        result = ve.auth_plain({'user': 'pass'})
        
        self.assertTrue(result)

    def test_auth_plain_failure(self):
        ve, sock = self._create_veencrypt()
        sock.send.reset_mock()
        
        # 4 byte user, 5 byte pass (wrong)
        sock.recv.side_effect = [
            b'\x00\x00\x00\x04\x00\x00\x00\x05',  # header
            b'user',                               # username
            b'wrong',                              # wrong password
        ]
        
        result = ve.auth_plain({'user': 'pass'})
        
        self.assertFalse(result)

    def test_auth_plain_multiple_users(self):
        ve, sock = self._create_veencrypt()
        sock.send.reset_mock()
        
        # 3 byte user ("usr"), 5 byte pass ("pass2")
        sock.recv.side_effect = [
            b'\x00\x00\x00\x03\x00\x00\x00\x05',  # header
            b'usr',                                # username (3 bytes, but userlist key is "user2")
            b'pass2',                              # password
        ]
        
        result = ve.auth_plain({'user1': 'pass1', 'usr': 'pass2'})
        
        self.assertTrue(result)

    def test_get_socket_plain(self):
        ve, sock = self._create_veencrypt()
        result = ve.get_socket()
        self.assertEqual(result, sock)

    def test_get_socket_tls(self):
        ve, sock = self._create_veencrypt()
        mock_ssl = Mock()
        ve.ssl_socket = mock_ssl
        result = ve.get_socket()
        self.assertEqual(result, mock_ssl)

    @patch('lib.auth.vencrypt.ssl')
    @patch('lib.auth.vencrypt.os')
    def test_load_ssl_context_with_cert(self, mock_os, mock_ssl):
        mock_os.path.isfile.return_value = True
        mock_os.path.abspath.return_value = '/test/path'
        mock_os.path.dirname.return_value = '/test'
        
        mock_sslctx = Mock()
        mock_ssl.SSLContext.return_value = mock_sslctx
        
        ve, _ = self._create_veencrypt()
        ctx = ve._load_ssl_context('/path/to/cert.pem')
        
        mock_ssl.SSLContext.assert_called_once()
        mock_sslctx.load_cert_chain.assert_called_with(certfile='/path/to/cert.pem')

    @patch('lib.auth.vencrypt.ssl')
    def test_load_ssl_context_no_cert(self, mock_ssl):
        mock_sslctx = Mock()
        mock_ssl.SSLContext.return_value = mock_sslctx
        
        ve, _ = self._create_veencrypt()
        ve._generate_self_signed_cert = Mock(return_value='/gen.pem')
        
        ctx = ve._load_ssl_context(None)
        
        ve._generate_self_signed_cert.assert_called_once()

    def test_auth_plain_empty_username(self):
        ve, sock = self._create_veencrypt()
        sock.send.reset_mock()
        
        # _recv_exact(0) returns immediately without calling recv
        # _recv_exact(4) calls recv once
        # So we only need 2 items in side_effect: header + password
        sock.recv.side_effect = [
            b'\x00\x00\x00\x00\x00\x00\x00\x04',  # header: 0 byte user, 4 byte pass
            b'pass',                               # password (recv(4))
        ]
        
        result = ve.auth_plain({'': 'pass'})
        self.assertTrue(result)


class TestVNCServerVeNCrypt(unittest.TestCase):
    """Tests for VNCServer integration with VeNCrypt auth."""

    def _make_server(self, mock_auth, mock_grab):
        """Helper to create a VNCServer with mocked dependencies."""
        socket_mock = Mock()
        socket_mock.recv.side_effect = [
            b"RFB 003.008\n",   # client version (get_buffer 30 bytes)
            b"\x13",             # security type 19 (get_buffer 30 bytes)
            b"\x00",             # ClientInit byte (server.recv(1))
        ]
        socket_mock.send = Mock()
        
        config = Mock()
        config.win_title = "Test"
        config.eightbitdither = False
        
        server = VNCServer(
            socket=socket_mock,
            password='user:pass',
            auth_type=19,
            vnc_config=config
        )
        
        # Patch VeNCrypt properly - mock_auth must have SUBTYPE_PLAIN and SUBTYPE_TLSPLAIN
        # constants so VeNCrypt.SUBTYPE_PLAIN in server.py resolves to real values
        mock_auth.SUBTYPE_PLAIN = 256
        mock_auth.SUBTYPE_TLSPLAIN = 259
        mock_auth.send_subtypes = Mock()  # prevent actual recv call
        
        # Create a mock class that returns mock_auth and has the constants
        mock_ve_class = Mock()
        mock_ve_class.return_value = mock_auth
        mock_ve_class.SUBTYPE_PLAIN = 256
        mock_ve_class.SUBTYPE_TLSPLAIN = 259
        
        patcher_ve = patch('pyvncs.server.VeNCrypt', mock_ve_class)
        patcher_grab = patch('pyvncs.server.ImageGrab.grab', return_value=Mock(size=(1024, 768)))
        patcher_ve.start()
        patcher_grab.start()
        
        return server, socket_mock, patcher_ve, patcher_grab

    def _teardown(self, patcher_ve, patcher_grab):
        patcher_ve.stop()
        patcher_grab.stop()

    def test_init_vencrypt_plain(self):
        mock_auth = Mock()
        mock_auth.client_subtype = 256
        mock_auth.auth_plain.return_value = True
        mock_auth.get_socket.return_value = Mock()
        
        server, sock, pv, pg = self._make_server(mock_auth, Mock())
        
        result = server.init()
        
        self.assertTrue(result)
        mock_auth.auth_plain.assert_called_once()
        self._teardown(pv, pg)

    def test_init_vencrypt_tls(self):
        mock_auth = Mock()
        mock_auth.client_subtype = 259
        mock_auth.auth_tls_plain.return_value = True
        mock_ssl = Mock()
        mock_auth.get_socket.return_value = mock_ssl
        
        server, sock, pv, pg = self._make_server(mock_auth, Mock())
        
        result = server.init()
        
        self.assertTrue(result)
        mock_auth.auth_tls_plain.assert_called_once()
        self._teardown(pv, pg)

    def test_init_vencrypt_auth_failure(self):
        mock_auth = Mock()
        mock_auth.client_subtype = 256
        mock_auth.auth_plain.return_value = False
        
        server, sock, pv, pg = self._make_server(mock_auth, Mock())
        
        result = server.init()
        
        self.assertFalse(result)
        self._teardown(pv, pg)

    def test_init_vencrypt_unsupported_subtype(self):
        mock_auth = Mock()
        mock_auth.client_subtype = 999
        
        server, sock, pv, pg = self._make_server(mock_auth, Mock())
        
        result = server.init()
        
        self.assertFalse(result)
        self._teardown(pv, pg)

    def test_init_vencrypt_tls_socket_replacement(self):
        mock_auth = Mock()
        mock_auth.client_subtype = 259
        mock_auth.auth_tls_plain.return_value = True
        mock_ssl = Mock()
        mock_auth.get_socket.return_value = mock_ssl
        
        server, sock, pv, pg = self._make_server(mock_auth, Mock())
        
        server.init()
        
        # After TLS, server.socket should be the SSL socket
        self.assertEqual(server.socket, mock_ssl)
        self._teardown(pv, pg)


class TestVeNCryptAuthPlain(unittest.TestCase):
    """Detailed tests for plain auth message parsing."""

    def _create_veencrypt(self, client_version=b'\x00\x02'):
        sock_mock = Mock()
        sock_mock.recv.return_value = client_version
        sock_mock.send = Mock()
        return VeNCrypt(sock_mock), sock_mock

    def test_auth_plain_single_user(self):
        ve, sock = self._create_veencrypt()
        sock.send.reset_mock()
        
        sock.recv.side_effect = [
            b'\x00\x00\x00\x04\x00\x00\x00\x06',  # header: 4 byte user, 6 byte pass
            b'user',
            b'mypass',
        ]
        
        result = ve.auth_plain({'user': 'mypass'})
        self.assertTrue(result)

    def test_send_auth_result_success(self):
        from struct import unpack
        ve, sock = self._create_veencrypt()
        sock.sendall.reset_mock()
        ve._send_auth_result(True)
        
        self.assertTrue(sock.sendall.called)
        call_args = sock.sendall.call_args[0][0]
        value = unpack('!I', call_args)[0]
        self.assertEqual(value, 0)

    def test_send_auth_result_failure(self):
        from struct import unpack
        ve, sock = self._create_veencrypt()
        sock.sendall.reset_mock()
        ve._send_auth_result(False)
        
        self.assertTrue(sock.sendall.called)
        call_args = sock.sendall.call_args[0][0]
        value = unpack('!I', call_args)[0]
        self.assertEqual(value, 1)


if __name__ == '__main__':
    unittest.main()
