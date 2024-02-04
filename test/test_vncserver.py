import unittest
from unittest.mock import Mock, patch
from pyvncs.server import VNCServer

class TestVNCServer(unittest.TestCase):

    def setUp(self):
        self.socket_mock = Mock()
        self.vnc_config_mock = Mock()
        self.vnc_config_mock.win_title = "Test Window Title"
        self.vnc_server = VNCServer(socket=self.socket_mock, password="test_password", auth_type=2, vnc_config=self.vnc_config_mock)

    def test_constructor(self):
        self.assertEqual(self.vnc_server.password, "test_password")
        self.assertIsNotNone(self.vnc_server.socket)

    def test_send_message(self):
        message = "Test message"
        encoded_message = bytes(message, 'iso8859-1')
        message_length = len(encoded_message)
        with patch('struct.pack') as mock_pack:
            mock_pack.return_value = b''  # Simulate the packed message
            self.vnc_server.send_message(message)
            mock_pack.assert_called()  # Verificar que se llamó, pero no con argumentos específicos
            self.socket_mock.send.assert_called()  # Verificar que se envió el mensaje

    def test_get_buffer_timeout(self):
        self.socket_mock.recv.side_effect = TimeoutError
        result = self.vnc_server.get_buffer(30)
        self.assertIsNone(result)

    def test_init(self):
        # Mock the responses for version handshake and security type handshake
        self.socket_mock.recv.side_effect = [
            b"RFB 003.008\n",  # Client version response
            b"\x02"           # Security type response (VNCAuth)
        ]

        # Mock ImageGrab.grab() called in server_init
        with patch('pyvncs.server.ImageGrab.grab') as mock_grab:
            mock_grab.return_value.size = (1024, 768)
            # Mock any other dependencies in server_init here if necessary

            result = self.vnc_server.init()
            self.assertTrue(result)

    def test_server_init(self):
        with patch('pyvncs.server.ImageGrab.grab') as mock_grab:
            mock_grab.return_value.size = (1024, 768)
            self.vnc_server.server_init()
            self.assertEqual(self.vnc_server.width, 1024)
            self.assertEqual(self.vnc_server.height, 768)

    def test_handle_client_keyboard_event(self):
        # Simulate a keyboard event
        self.socket_mock.recv.side_effect = [
            b'\x04',  # Indicating a keyboard event
            b'additional data for the keyboard event'
        ]
        with patch('pyvncs.server.kbdctrl.KeyboardController.process_event') as mock_kbd_event:
            self.vnc_server.handle_client()
            mock_kbd_event.assert_called_with(b'additional data for the keyboard event')

    # TODO: Agregar más pruebas según sea necesario

if __name__ == '__main__':
    unittest.main()
