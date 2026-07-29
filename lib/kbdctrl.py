import os
from struct import unpack
from lib import log

# ---------------------------------------------------------------------------
# Optional backends
# ---------------------------------------------------------------------------

try:
    from pynput import keyboard
    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False

try:
    import evdev
    from evdev import ecodes
    _EVDEV_AVAILABLE = True
except ImportError:
    _EVDEV_AVAILABLE = False

try:
    from Xlib.display import Display as _XlibDisplay
    from Xlib import X as _X
    _XLIB_AVAILABLE = True
except ImportError:
    _XLIB_AVAILABLE = False


def _is_wayland():
    return bool(os.environ.get('WAYLAND_DISPLAY'))


# ---------------------------------------------------------------------------
# Fallback keysym → Linux keycode for special keys and when Xlib is
# unavailable.  These keys have the same physical position on ALL layouts.
# ---------------------------------------------------------------------------

_FALLBACK_MAP = {
    0xff08: 'KEY_BACKSPACE', 0xff09: 'KEY_TAB',     0xff0d: 'KEY_ENTER',
    0xff1b: 'KEY_ESC',       0xff63: 'KEY_INSERT',   0xffff: 'KEY_DELETE',
    0xff50: 'KEY_HOME',      0xff57: 'KEY_END',
    0xff55: 'KEY_PAGEUP',    0xff56: 'KEY_PAGEDOWN',
    0xff51: 'KEY_LEFT',      0xff52: 'KEY_UP',
    0xff53: 'KEY_RIGHT',     0xff54: 'KEY_DOWN',
    0xffe1: 'KEY_LEFTSHIFT', 0xffe2: 'KEY_RIGHTSHIFT',
    0xffe3: 'KEY_LEFTCTRL',  0xffe4: 'KEY_RIGHTCTRL',
    0xffe7: 'KEY_LEFTMETA',  0xffe8: 'KEY_RIGHTMETA',
    0xffe9: 'KEY_LEFTALT',   0xffea: 'KEY_RIGHTALT',
    0xfe03: 'KEY_RIGHTALT',
    0xffeb: 'KEY_LEFTMETA',  0xffec: 'KEY_RIGHTMETA',
    0xffe5: 'KEY_CAPSLOCK',  0xff7f: 'KEY_NUMLOCK',
    0xff14: 'KEY_SCROLLLOCK',
    0xff13: 'KEY_PAUSE',     0xff15: 'KEY_SYSRQ',
}

# Add F1-F24
for _i in range(1, 25):
    _FALLBACK_MAP[0xffbe + _i - 1] = 'KEY_F%d' % _i

# Modifier keysyms we track for state
_SHIFT_KEYSYMS = {0xffe1, 0xffe2}
_ALTGR_KEYSYMS = {0xffea, 0xfe03}


class KeyboardController:

    def __init__(self):
        self._backend = 'none'
        self._evdev_ui = None
        self._x_display = None
        self._x_keymap = None
        self._x_min_keycode = 8
        self._shift_held = False
        self._altgr_held = False
        self._auto_shift = False
        self._auto_altgr = False
        self._pynput_ctrl = None

        if _is_wayland() and _EVDEV_AVAILABLE:
            try:
                self._evdev_ui = evdev.UInput(name='pyvncs-keyboard')
                if _XLIB_AVAILABLE:
                    self._x_display = _XlibDisplay()
                    self._load_x_keymap()
                self._backend = 'evdev'
                log.debug("KeyboardController: evdev/uinput (Wayland)")
                return
            except Exception as e:
                log.debug("KeyboardController: evdev init failed (%s), "
                          "trying pynput" % e)

        if _PYNPUT_AVAILABLE:
            self._pynput_ctrl = keyboard.Controller()
            self._backend = 'pynput'
            log.debug("KeyboardController: pynput/X11")
        else:
            log.debug("KeyboardController: no backend available")

    # -- X keyboard mapping -------------------------------------------------

    def _load_x_keymap(self):
        """Fetch the full keyboard mapping from the X server.

        The mapping contains keysyms for ALL configured keyboard groups
        (e.g. ``latam,es,es``).  We only want group 0 (the primary layout),
        so we compute how many keysyms belong to each group and restrict
        the search accordingly.
        """
        info = self._x_display.display.info
        self._x_min_keycode = info.min_keycode
        count = info.max_keycode - info.min_keycode + 1
        self._x_keymap = self._x_display.get_keyboard_mapping(
            self._x_min_keycode, count)

        # Determine keysyms_per_keycode from the first non-empty entry.
        self._keysyms_per_kc = 0
        for syms in self._x_keymap:
            n = len(syms) if hasattr(syms, '__len__') else 1
            if n > self._keysyms_per_kc:
                self._keysyms_per_kc = n

        # Number of groups from XKB_DEFAULT_LAYOUT (e.g. "latam,es,es" → 3).
        layout = os.environ.get('XKB_DEFAULT_LAYOUT', '')
        self._num_groups = len(layout.split(',')) if layout else 1

        # Levels per group (typically 4: base, Shift, AltGr, Shift+AltGr).
        if self._num_groups > 0 and self._keysyms_per_kc > 0:
            self._levels_per_group = self._keysyms_per_kc // self._num_groups
        else:
            self._levels_per_group = self._keysyms_per_kc

    def _lookup_keysym(self, keysym):
        """Find the Linux keycode and modifier flags for *keysym*.

        Only searches **group 0** (the primary keyboard layout) so that
        the result matches what the Wayland compositor actually produces.

        Returns ``(linux_keycode, needs_shift, needs_altgr)`` or
        ``(None, False, False)`` if not found.
        """
        # 1. Search the live X keyboard mapping — group 0 only.
        if self._x_keymap is not None:
            lpg = self._levels_per_group
            for idx, syms in enumerate(self._x_keymap):
                if isinstance(syms, int):
                    syms = (syms,)
                # Restrict to group 0 (first lpg keysyms).
                group0 = syms[:lpg] if lpg > 0 else syms
                for level, sym in enumerate(group0):
                    if sym == keysym:
                        x_kc = idx + self._x_min_keycode
                        return x_kc - 8, level % 2 == 1, level >= 2

        # 2. Fallback static table (special keys only).
        name = _FALLBACK_MAP.get(keysym)
        if name:
            kc = getattr(ecodes, name, None)
            if kc is not None:
                return kc, False, False

        return None, False, False

    # -- Public API ---------------------------------------------------------

    def process_event(self, data):
        if len(data) < 7:
            log.debug("KeyboardController: short data (%d bytes)" % len(data))
            return

        (downflag, key) = unpack("!BxxL", data)

        # Track modifier state (sent by the client as separate events).
        if key in _SHIFT_KEYSYMS:
            self._shift_held = bool(downflag)
        elif key in _ALTGR_KEYSYMS:
            self._altgr_held = bool(downflag)

        if self._backend == 'evdev':
            self._inject_evdev(key, bool(downflag))
        elif self._backend == 'pynput':
            self._inject_pynput(key, bool(downflag))

    # -- evdev injection ----------------------------------------------------

    def _inject_evdev(self, keysym, down):
        keycode, needs_shift, needs_altgr = self._lookup_keysym(keysym)
        if keycode is None:
            log.debug("KeyboardController: no keycode for keysym 0x%x" % keysym)
            return

        try:
            if down:
                # Press modifiers that the key needs but the client hasn't
                # already pressed.
                if needs_shift and not self._shift_held:
                    self._write_key(ecodes.KEY_LEFTSHIFT, 1)
                    self._auto_shift = True
                if needs_altgr and not self._altgr_held:
                    self._write_key(ecodes.KEY_RIGHTALT, 1)
                    self._auto_altgr = True
                self._write_key(keycode, 1)
            else:
                self._write_key(keycode, 0)
                # Release any modifier we injected for this key.
                if self._auto_shift:
                    self._write_key(ecodes.KEY_LEFTSHIFT, 0)
                    self._auto_shift = False
                if self._auto_altgr:
                    self._write_key(ecodes.KEY_RIGHTALT, 0)
                    self._auto_altgr = False
        except Exception as e:
            log.debug("KeyboardController: evdev write error: %s" % e)

    def _write_key(self, keycode, value):
        self._evdev_ui.write(ecodes.EV_KEY, keycode, value)
        self._evdev_ui.syn()

    # -- pynput fallback ----------------------------------------------------

    def _inject_pynput(self, keysym, down):
        from lib.kbdmap import kbdmap

        if keysym in kbdmap:
            kbdkey = kbdmap[keysym]
        else:
            try:
                kbdkey = keyboard.KeyCode.from_char(chr(keysym))
            except Exception:
                kbdkey = None

        if kbdkey is None:
            return

        try:
            if down:
                self._pynput_ctrl.press(kbdkey)
            else:
                self._pynput_ctrl.release(kbdkey)
        except Exception as e:
            log.debug("KeyboardController: pynput error: %s" % e)

    # -- cleanup ------------------------------------------------------------

    def close(self):
        if self._evdev_ui is not None:
            try:
                self._evdev_ui.close()
            except Exception:
                pass
            self._evdev_ui = None
        if self._x_display is not None:
            try:
                self._x_display.close()
            except Exception:
                pass
            self._x_display = None

    def __del__(self):
        self.close()
