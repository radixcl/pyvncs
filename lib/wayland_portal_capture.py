import os
import threading
import uuid

import dbus
import gi
gi.require_version('Gst', '1.0')
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib, Gst
from PIL import Image
import numpy as np

from lib import log

__all__ = ['WaylandPortalCapture', 'get_instance']

PORTAL_BUS_NAME = 'org.freedesktop.portal.Desktop'
PORTAL_OBJECT_PATH = '/org/freedesktop/portal/desktop'
SCREENCAST_IFACE = 'org.freedesktop.portal.ScreenCast'
REQUEST_IFACE = 'org.freedesktop.portal.Request'

SOURCE_TYPE_MONITOR = 1
CURSOR_MODE_HIDDEN = 1
PERSIST_MODE_UNTIL_REVOKED = 2

FIRST_FRAME_TIMEOUT = 90


def _config_dir():
    return os.path.join(os.path.expanduser('~'), '.config', 'pyvncs')


def _token_path():
    return os.path.join(_config_dir(), 'wayland_restore_token')


def _load_restore_token():
    try:
        with open(_token_path(), 'r') as f:
            token = f.read().strip()
            return token or None
    except OSError:
        return None


def _save_restore_token(token):
    directory = _config_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = _token_path()
    with open(path, 'w') as f:
        f.write(token)
    os.chmod(path, 0o600)


class WaylandPortalCapture():
    """Captura de pantalla en Wayland (GNOME/KDE) via xdg-desktop-portal ScreenCast + PipeWire."""

    def __init__(self):
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._started = False
        self._frame = None
        self._error = None
        self._first_frame_event = threading.Event()
        self._bus = None
        self._screencast = None
        self._session_handle = None
        self._pipeline = None

    def grab(self):
        self._ensure_started()
        if not self._first_frame_event.is_set():
            log.debug('Esperando a que se apruebe el dialogo de compartir pantalla en el escritorio...')
            if not self._first_frame_event.wait(timeout=FIRST_FRAME_TIMEOUT):
                raise TimeoutError(
                    'Tiempo de espera agotado esperando el primer frame de captura Wayland '
                    '(verificar que se aprobo el dialogo de compartir pantalla)'
                )
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._frame is None:
                raise RuntimeError('No se pudo obtener un frame de la captura Wayland')
            return self._frame.copy()

    def _ensure_started(self):
        with self._start_lock:
            if self._started:
                return
            self._started = True
            thread = threading.Thread(target=self._run, name='wayland-portal-capture', daemon=True)
            thread.start()

    def _fail(self, exc):
        log.error(str(exc))
        with self._lock:
            self._error = exc
        self._first_frame_event.set()

    def _run(self):
        try:
            DBusGMainLoop(set_as_default=True)
            Gst.init(None)
            self._bus = dbus.SessionBus()
            portal = self._bus.get_object(PORTAL_BUS_NAME, PORTAL_OBJECT_PATH)
            self._screencast = dbus.Interface(portal, SCREENCAST_IFACE)
            self._create_session()
            GLib.MainLoop().run()
        except Exception as e:
            self._fail(e)

    def _subscribe_request(self, on_response):
        token = 'pyvncs_%s' % uuid.uuid4().hex
        sender = self._bus.get_unique_name().lstrip(':').replace('.', '_')
        request_path = '/org/freedesktop/portal/desktop/request/%s/%s' % (sender, token)

        def _handler(response, results):
            self._bus.remove_signal_receiver(
                _handler, signal_name='Response', dbus_interface=REQUEST_IFACE, path=request_path
            )
            if response != 0:
                self._fail(RuntimeError(
                    'El portal de captura de pantalla rechazo o cancelo la solicitud (codigo %d)' % response
                ))
                return
            on_response(results)

        self._bus.add_signal_receiver(
            _handler, signal_name='Response', dbus_interface=REQUEST_IFACE,
            path=request_path, bus_name=PORTAL_BUS_NAME
        )
        return token

    def _create_session(self):
        token = self._subscribe_request(self._on_session_created)
        options = {
            'handle_token': token,
            'session_handle_token': token,
        }
        self._screencast.CreateSession(options)

    def _on_session_created(self, results):
        self._session_handle = results['session_handle']
        self._select_sources()

    def _select_sources(self):
        token = self._subscribe_request(self._on_sources_selected)
        options = {
            'handle_token': token,
            'types': dbus.UInt32(SOURCE_TYPE_MONITOR),
            'multiple': False,
            'cursor_mode': dbus.UInt32(CURSOR_MODE_HIDDEN),
            'persist_mode': dbus.UInt32(PERSIST_MODE_UNTIL_REVOKED),
        }
        restore_token = _load_restore_token()
        if restore_token:
            options['restore_token'] = restore_token
        self._screencast.SelectSources(self._session_handle, options)

    def _on_sources_selected(self, results):
        self._start()

    def _start(self):
        token = self._subscribe_request(self._on_started)
        self._screencast.Start(self._session_handle, '', {'handle_token': token})

    def _on_started(self, results):
        streams = results.get('streams')
        if not streams:
            self._fail(RuntimeError('El portal no devolvio ningun stream de captura'))
            return
        node_id, _props = streams[0]
        new_token = results.get('restore_token')
        if new_token:
            _save_restore_token(str(new_token))
        self._open_pipewire_remote(int(node_id))

    def _open_pipewire_remote(self, node_id):
        fd_object = self._screencast.OpenPipeWireRemote(self._session_handle, {})
        fd = fd_object.take()
        self._start_pipeline(fd, node_id)

    def _start_pipeline(self, fd, node_id):
        pipeline_str = (
            'pipewiresrc fd=%d path=%d ! videoconvert ! '
            'video/x-raw,format=RGB ! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false'
            % (fd, node_id)
        )
        self._pipeline = Gst.parse_launch(pipeline_str)
        sink = self._pipeline.get_by_name('sink')
        sink.connect('new-sample', self._on_new_sample)

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message::error', self._on_gst_error)

        self._pipeline.set_state(Gst.State.PLAYING)

    def _on_gst_error(self, _bus, message):
        err, debug = message.parse_error()
        self._fail(RuntimeError('Error de GStreamer: %s (%s)' % (err, debug)))

    def _on_new_sample(self, sink):
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        structure = sample.get_caps().get_structure(0)
        width = structure.get_value('width')
        height = structure.get_value('height')

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            stride = (width * 3 + 3) & ~3
            data = np.frombuffer(mapinfo.data, dtype=np.uint8)
            data = data[:stride * height].reshape(height, stride)[:, :width * 3].reshape(height, width, 3)
            image = Image.fromarray(data, 'RGB')
        finally:
            buf.unmap(mapinfo)

        with self._lock:
            self._frame = image
            self._error = None
        self._first_frame_event.set()
        return Gst.FlowReturn.OK


_instance = None
_instance_lock = threading.Lock()


def get_instance():
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = WaylandPortalCapture()
        return _instance
