import os
import sys
from PIL import Image
from lib import log

_x11_display = None
_x11_root = None
_x11_geom = None


class ImageGrab():
    @staticmethod
    def _detect_wlroots_compositor():
        app_id = os.environ.get('XDG_SESSION_DESKTOP', '') + os.environ.get('DESKTOP_SESSION', '')
        if 'sway' in app_id.lower():
            return 'sway'
        if 'hyprland' in app_id.lower():
            return 'hyprland'
        if 'wayfire' in app_id.lower():
            return 'wayfire'
        return None

    @staticmethod
    def _is_wayland_session():
        wayland_display = os.environ.get('WAYLAND_DISPLAY', '')
        session_type = os.environ.get('XDG_SESSION_TYPE', '')
        display = os.environ.get('DISPLAY', '')

        #log.debug(f"ImageGrab: WAYLAND_DISPLAY={repr(wayland_display)} "
        #          f"XDG_SESSION_TYPE={repr(session_type)} DISPLAY={repr(display)}")

        # Wayland takes priority if WAYLAND_DISPLAY is set
        if wayland_display:
            return True

        # Fallback: check session type
        if session_type == 'wayland':
            return True
        if session_type == 'x11':
            return False

        # Detect wlroots-based compositors from session name
        compositor = ImageGrab._detect_wlroots_compositor()
        if compositor:
            log.debug(f"ImageGrab: detected wlroots compositor: {compositor}")
            return True

        # If only DISPLAY is set (no WAYLAND_DISPLAY), assume X11
        if display and not wayland_display:
            return False

        return False

    @staticmethod
    def _get_x11_root():
        global _x11_display, _x11_root, _x11_geom
        if _x11_root is None:
            from Xlib import display
            _x11_display = display.Display()
            _x11_root = _x11_display.screen().root
            _x11_geom = _x11_root.get_geometry()
        return _x11_root, _x11_geom

    @staticmethod
    def grab():
        if sys.platform == "linux" or sys.platform == "linux2":
            is_wl = ImageGrab._is_wayland_session()

            if not is_wl:
                try:
                    from Xlib import X
                    root, geom = ImageGrab._get_x11_root()
                    w = geom.width
                    h = geom.height
                    raw = root.get_image(0, 0, w, h, X.ZPixmap, 0xffffffff)
                    import numpy as np
                    arr = np.frombuffer(raw.data, dtype=np.uint8).reshape(h, w, 4)[:, :, :3]
                    return arr[:, :, ::-1].copy()
                except Exception as e:
                    log.debug(f"ImageGrab: X11 capture failed: {e}")

            if is_wl:
                try:
                    from lib.wayland_portal_capture import (
                        check_dependencies, get_instance, CAPTURE_MODE_MONITORS
                    )
                    deps = check_dependencies()
                    if deps:
                        log.error('Dependencias faltantes para captura Wayland:')
                        for m in deps:
                            log.error('  - ' + m)
                        raise RuntimeError(
                            'Faltan dependencias para captura Wayland (ver logs)'
                        )
                    mode = os.environ.get('PYVNCS_WAYLAND_CAPTURE', CAPTURE_MODE_MONITORS)
                    return get_instance(capture_mode=mode).grab()
                except Exception as e:
                    log.debug(f"ImageGrab: Wayland portal capture failed: {e}")

            try:
                from lib.imagegrab_wayland import WaylandImageGrab
                return WaylandImageGrab.grab()
            except Exception as e:
                log.debug(f"ImageGrab: Wayland capture failed: {e}")
                raise EnvironmentError(
                    "Screen capture unavailable. "
                    "Ensure X11 (DISPLAY) or Wayland (WAYLAND_DISPLAY + xdg-desktop-portal) "
                    "is configured, and pipewire/portal packages are installed for Wayland."
                )

        elif sys.platform == "darwin":
            import Quartz.CoreGraphics as CG
            import numpy as np
            screenshot = CG.CGWindowListCreateImage(CG.CGRectInfinite, CG.kCGWindowListOptionOnScreenOnly, CG.kCGNullWindowID, CG.kCGWindowImageDefault)
            width = CG.CGImageGetWidth(screenshot)
            height = CG.CGImageGetHeight(screenshot)
            bytesperrow = CG.CGImageGetBytesPerRow(screenshot)

            pixeldata = CG.CGDataProviderCopyData(CG.CGImageGetDataProvider(screenshot))
            arr = np.frombuffer(pixeldata, dtype=np.uint8).reshape(height, bytesperrow)
            arr = arr[:, :width * 4].reshape(height, width, 4)
            return arr[:, :, [2, 1, 0]].copy()

        elif sys.platform == "win32":
            import numpy as np
            from PIL import ImageGrab as WinImageGrab
            img = WinImageGrab.grab()
            arr = np.asarray(img)
            if arr.shape[2] == 4:
                arr = arr[:, :, :3]
            return arr.copy()

        else:
            log.debug("ImageGrab: running on an unknown platform!")
            raise EnvironmentError("Unsupported platform")
