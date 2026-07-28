import os
import threading
import time
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


def check_dependencies():
    """Verifica dependencias Python y del sistema para captura Wayland.

    Retorna una lista de mensajes de error vacia si todo esta OK,
    o con descripciones de lo que falta.
    """
    missing = []

    # --- Python packages ---
    try:
        import dbus  # noqa: F401
    except ImportError:
        missing.append('dbus-python (pip install dbus-python)')
    try:
        import gi  # noqa: F401
    except ImportError:
        missing.append('PyGObject (pip install PyGObject)')
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append('numpy (pip install numpy)')

    # GStreamer se verifica despues de importar gi
    _gi_ok = False
    try:
        import gi as _gi_mod  # noqa: F401
        _gi_ok = True
    except ImportError:
        missing.append('PyGObject (pip install PyGObject)')
    if _gi_ok:
        try:
            gi.require_version('Gst', '1.0')
            from gi.repository import Gst  # noqa: F401
        except (ImportError, KeyError):
            missing.append(
                'gstreamer1.0 + gir1.2-gst-1.0 (pip install PyGObject; '
                'system: gstreamer1.0-plugins-base, gir1.2-gst-1.0)'
            )

    # --- System binaries / portals ---
    for cmd in ('pipewire', 'xdg-desktop-portal', 'gdbus'):
        if not os.environ.get('PYVNCS_SKIP_SYS_CHECK') and not _which(cmd):
            missing.append(
                '%s (system package: pipewire, xdg-desktop-portal, dbus-x11)' % cmd
            )

    # Portal ScreenCast availability (requires running session)
    if not missing and os.environ.get('WAYLAND_DISPLAY'):
        try:
            bus = dbus.SessionBus()
            bus.get_object(PORTAL_BUS_NAME, PORTAL_OBJECT_PATH)
        except Exception:
            missing.append(
                'xdg-desktop-portal no responde en esta sesion (ejecuta: '
                'systemctl --user start xdg-desktop-portal)'
            )

    return missing


def _which(cmd):
    try:
        import shutil
        if shutil.which(cmd) is not None:
            return True
        for p in ['/usr/bin', '/bin', '/usr/sbin', '/sbin', '/usr/libexec']:
            candidate = os.path.join(p, cmd)
            if os.path.isfile(candidate):
                return True
        return False
    except Exception:
        return False

PORTAL_BUS_NAME = 'org.freedesktop.portal.Desktop'
PORTAL_OBJECT_PATH = '/org/freedesktop/portal/desktop'
SCREENCAST_IFACE = 'org.freedesktop.portal.ScreenCast'
REQUEST_IFACE = 'org.freedesktop.portal.Request'
SESSION_IFACE = 'org.freedesktop.portal.Session'

SOURCE_TYPE_MONITOR = 1
SOURCE_TYPE_WINDOW = 2
CURSOR_MODE_HIDDEN = 1
PERSIST_MODE_UNTIL_REVOKED = 2

FIRST_FRAME_TIMEOUT = 90
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 2.0

CAPTURE_MODE_MONITORS = 'monitors'
CAPTURE_MODE_WINDOWS = 'windows'


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


def _pick_best_stream(streams):
    """Selecciona el mejor stream de una lista (mayor resolución primero)."""
    if len(streams) == 1:
        return streams[0]
    def _resolution(s):
        props = s[1] if len(s) > 1 else {}
        width = props.get('width', 0) or 0
        height = props.get('height', 0) or 0
        return width * height
    return max(streams, key=_resolution)


class WaylandPortalCapture():
    """Captura de pantalla en Wayland (GNOME/KDE) via xdg-desktop-portal ScreenCast + PipeWire.

    Soporta multi-monitor y captura de ventanas individuales.
    Incluye reconexión automática ante caída del stream o revocación del portal.
    """

    def __init__(self, capture_mode=CAPTURE_MODE_MONITORS):
        self._capture_mode = capture_mode
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._started = False
        self._stopping = False
        self._frame = None
        self._error = None
        self._first_frame_event = threading.Event()
        self._bus = None
        self._screencast = None
        self._session_handle = None
        self._pipeline = None
        self._main_loop = None
        self._reconnect_attempts = 0

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

    def stop(self):
        """Detiene la captura y libera recursos."""
        with self._start_lock:
            self._stopping = True
            if self._pipeline is not None:
                try:
                    self._pipeline.set_state(Gst.State.NULL)
                except Exception:
                    pass
                self._pipeline = None
            if self._main_loop is not None:
                try:
                    self._main_loop.quit()
                except Exception:
                    pass
                self._main_loop = None
            self._started = False
            self._reconnect_attempts = 0

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
            self._main_loop = GLib.MainLoop()
            self._create_session()
            self._main_loop.run()
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
        # Escuchar revocación del portal
        try:
            self._bus.add_signal_receiver(
                self._on_portal_revoked,
                signal_name='Closed',
                dbus_interface=SESSION_IFACE,
                path=self._session_handle,
                bus_name=PORTAL_BUS_NAME,
            )
        except Exception:
            pass
        self._select_sources()

    def _select_sources(self):
        token = self._subscribe_request(self._on_sources_selected)
        if self._capture_mode == CAPTURE_MODE_WINDOWS:
            source_types = SOURCE_TYPE_WINDOW
        else:
            source_types = SOURCE_TYPE_MONITOR
        options = {
            'handle_token': token,
            'types': dbus.UInt32(source_types),
            'multiple': True,
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
        node_id, _props = _pick_best_stream(streams)
        new_token = results.get('restore_token')
        if new_token:
            _save_restore_token(str(new_token))
        self._reconnect_attempts = 0
        self._open_pipewire_remote(int(node_id))

    def _on_portal_revoked(self):
        log.warning('El portal revoco el stream de captura, intentando reconectar...')
        self._attempt_reconnect()

    def _attempt_reconnect(self):
        if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            self._fail(RuntimeError(
                'No se pudo reconectar despues de %d intentos' % MAX_RECONNECT_ATTEMPTS
            ))
            return
        self._reconnect_attempts += 1
        log.info('Reconectando al portal (intento %d/%d)...', self._reconnect_attempts, MAX_RECONNECT_ATTEMPTS)
        time.sleep(RECONNECT_DELAY)
        try:
            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
                self._pipeline = None
            self._start()
        except Exception as e:
            log.error('Error en reconexion: %s', e)
            self._attempt_reconnect()

    def _on_pipeline_eos(self, _bus, message):
        log.warning('PipeWire stream termino (EOS), intentando reconectar...')
        self._attempt_reconnect()

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
        bus.connect('message::eos', self._on_pipeline_eos)

        self._pipeline.set_state(Gst.State.PLAYING)

    def _on_gst_error(self, _bus, message):
        err, debug = message.parse_error()
        log.error('Error de GStreamer: %s (%s)', err, debug)
        self._attempt_reconnect()

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


def get_instance(capture_mode=CAPTURE_MODE_MONITORS):
    global _instance
    with _instance_lock:
        if _instance is None or _instance._capture_mode != capture_mode:
            old = _instance
            _instance = WaylandPortalCapture(capture_mode=capture_mode)
            if old is not None:
                old.stop()
        return _instance
