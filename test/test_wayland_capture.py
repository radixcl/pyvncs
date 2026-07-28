import unittest
from unittest.mock import Mock, patch, MagicMock


class TestWaylandPortalCapture(unittest.TestCase):

    def setUp(self):
        import lib.wayland_portal_capture as m
        m._instance = None

    # --- _pick_best_stream ---

    def test_pick_best_stream_single(self):
        from lib.wayland_portal_capture import _pick_best_stream
        streams = [(100, {})]
        result = _pick_best_stream(streams)
        self.assertEqual(result[0], 100)

    def test_pick_best_stream_multiple(self):
        from lib.wayland_portal_capture import _pick_best_stream
        streams = [
            (100, {'width': 1920, 'height': 1080}),
            (200, {'width': 3840, 'height': 2160}),
            (300, {'width': 1280, 'height': 720}),
        ]
        result = _pick_best_stream(streams)
        self.assertEqual(result[0], 200)

    # --- select_sources mode ---

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_select_sources_monitors_mode(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import (
            WaylandPortalCapture, CAPTURE_MODE_MONITORS, SOURCE_TYPE_MONITOR
        )
        capture = WaylandPortalCapture(capture_mode=CAPTURE_MODE_MONITORS)
        capture._session_handle = '/mock/session'
        capture._screencast = Mock()
        capture._subscribe_request = Mock(side_effect=lambda cb: 'token')

        capture._select_sources()

        args, kwargs = capture._screencast.SelectSources.call_args
        options = args[1]
        self.assertIn('types', options)
        self.assertTrue(options['multiple'])

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_select_sources_windows_mode(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import (
            WaylandPortalCapture, CAPTURE_MODE_WINDOWS, SOURCE_TYPE_WINDOW
        )
        capture = WaylandPortalCapture(capture_mode=CAPTURE_MODE_WINDOWS)
        capture._session_handle = '/mock/session'
        capture._screencast = Mock()
        capture._subscribe_request = Mock(side_effect=lambda cb: 'token')

        capture._select_sources()

        args, kwargs = capture._screencast.SelectSources.call_args
        options = args[1]
        self.assertIn('types', options)
        self.assertTrue(options['multiple'])

    # --- _on_started picks best stream ---

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_on_started_picks_best_stream(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()
        capture._open_pipewire_remote = Mock()

        streams = [
            (100, {'width': 1920, 'height': 1080}),
            (200, {'width': 3840, 'height': 2160}),
        ]
        capture._on_started({'streams': streams})

        capture._open_pipewire_remote.assert_called_once_with(200)

    # --- reconnect logic ---

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_attempt_reconnect_succeeds(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()
        capture._start = Mock()

        with patch('time.sleep'):
            capture._attempt_reconnect()

        capture._start.assert_called_once()
        self.assertEqual(capture._reconnect_attempts, 1)

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_attempt_reconnect_max_exceeded(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()
        capture._reconnect_attempts = 5

        with patch('time.sleep'):
            capture._attempt_reconnect()

        self.assertIsNotNone(capture._error)
        self.assertIn('5 intentos', str(capture._error))

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_on_pipeline_eos_triggers_reconnect(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()
        capture._attempt_reconnect = Mock()

        capture._on_pipeline_eos(None, None)

        capture._attempt_reconnect.assert_called_once()

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_on_gst_error_triggers_reconnect(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()
        capture._attempt_reconnect = Mock()

        mock_message = Mock()
        mock_message.parse_error.return_value = ('test error', 'debug info')

        capture._on_gst_error(None, mock_message)

        capture._attempt_reconnect.assert_called_once()

    # --- stop ---

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_stop_clears_pipeline(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()
        pipeline_mock = Mock()
        main_loop_mock = Mock()
        capture._pipeline = pipeline_mock
        capture._main_loop = main_loop_mock

        capture.stop()

        pipeline_mock.set_state.assert_called_once()
        self.assertIsNone(capture._pipeline)
        self.assertFalse(capture._started)

    # --- singleton / mode switching ---

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_get_instance_returns_same_object(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import get_instance
        a = get_instance()
        b = get_instance()
        self.assertIs(a, b)

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_get_instance_switches_mode(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import get_instance, CAPTURE_MODE_MONITORS, CAPTURE_MODE_WINDOWS
        a = get_instance(capture_mode=CAPTURE_MODE_MONITORS)
        b = get_instance(capture_mode=CAPTURE_MODE_WINDOWS)
        self.assertIsNot(a, b)
        self.assertEqual(b._capture_mode, CAPTURE_MODE_WINDOWS)

    # --- _on_new_sample ---

    @patch('lib.wayland_portal_capture.Gst')
    @patch('lib.wayland_portal_capture.dbus')
    @patch('lib.wayland_portal_capture.DBusGMainLoop')
    @patch('lib.wayland_portal_capture.gi')
    def test_on_new_sample_sets_frame(self, mock_gi, mock_dbus, mock_gst, mock_loop):
        from lib.wayland_portal_capture import WaylandPortalCapture
        capture = WaylandPortalCapture()

        mock_sink = Mock()
        mock_sample = Mock()
        mock_buf = Mock()
        mock_structure = Mock()
        mock_caps = Mock()

        mock_structure.get_value.side_effect = lambda k: 1920 if k == 'width' else 1080
        mock_caps.get_structure.return_value = mock_structure
        mock_sample.get_caps.return_value = mock_caps
        mock_sample.get_buffer.return_value = mock_buf
        mock_sink.emit.return_value = mock_sample

        mock_mapinfo = Mock()
        mock_mapinfo.data = b'\x00' * (1920 * 3 * 1080)
        mock_buf.map.return_value = (True, mock_mapinfo)
        mock_buf.unmap = Mock()

        result = capture._on_new_sample(mock_sink)

        self.assertIsNotNone(capture._frame)
        self.assertTrue(capture._first_frame_event.is_set())
        self.assertIsNotNone(result)


if __name__ == '__main__':
    unittest.main()
