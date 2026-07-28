from struct import *
import sys
import threading
from lib import log

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, Gdk
    _GTK_AVAILABLE = True
except ImportError:
    _GTK_AVAILABLE = False


class ClipboardController():

    def __init__(self):
        self._last_clipboard = None
        self._lock = threading.Lock()
        self.sync_interval = 1.0  # seconds between clipboard sync attempts

    def client_cut_text(self, sock):
        """
        The client has new ISO 8859-1 (Latin-1) text in its cut buffer.
        Ends of lines are represented by the linefeed / newline character (value 10) alone. No carriage-return (value 13) is needed.

        No. of bytes	Type	[Value]	Description
        1	            U8      6       message-type
        3	 	 	                    padding
        4            U32	 	        length
        length	        U8 array	 	text
        """

        # read padding
        _ = sock.recv(3)

        # read length
        length = sock.recv(4)
        (length, ) = unpack('!I', length)

        # read text
        text = sock.recv(length)

        with self._lock:
            self._last_clipboard = text.decode('iso8859-1', errors='replace')
        return self._last_clipboard

    def get_server_clipboard(self):
        """Read the system clipboard content natively."""
        try:
            if sys.platform == 'win32':
                import tkinter as tk
                root = tk.Tk()
                root.withdraw()
                text = root.clipboard_get()
                root.destroy()
                return text
            elif sys.platform == 'darwin':
                import subprocess
                result = subprocess.run(
                    ['pbpaste'], capture_output=True, text=True, timeout=2
                )
                return result.stdout.strip()
            elif _GTK_AVAILABLE:
                d = Gdk.Display.get_default()
                if d is None:
                    return None
                atom = Gdk.atom_intern('CLIPBOARD', False)
                cb = Gtk.Clipboard.get_for_display(d, atom)
                text = cb.wait_for_text()
                return text
            else:
                return None
        except Exception as e:
            log.debug("Error reading server clipboard:", str(e))
        return None

    def maybe_send_clipboard(self, sock):
        """Check if system clipboard changed and send ServerCutText to client."""
        with self._lock:
            current = self._last_clipboard or ''

        try:
            server_text = self.get_server_clipboard()
            if server_text is None or server_text == '':
                return False

            if server_text == current:
                return False

            # Update our tracked clipboard
            with self._lock:
                self._last_clipboard = server_text

            # Send ServerCutText (message type 7)
            text_bytes = server_text.encode('iso8859-1')
            sendbuff = bytearray()
            sendbuff.append(7)  # ServerCutText
            sendbuff.extend(b'\x00\x00\x00')  # padding
            sendbuff.extend(pack('!I', len(text_bytes)))
            sendbuff.extend(text_bytes)

            sock.sendall(sendbuff)
            log.debug("Sent ServerCutText to client, length:", len(text_bytes))
            return True
        except Exception as e:
            log.debug("Error sending clipboard to client:", str(e))
            return False
