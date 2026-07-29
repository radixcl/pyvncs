# coding=utf-8
# pyvncs
# Copyright (C) 2017-2018 Matias Fernandez
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from struct import *
from pyDes import *
from time import sleep
from pynput import mouse, keyboard
from PIL import Image, ImageChops, ImageDraw, ImagePalette

import socket
import errno
import numpy as np
import time
import threading

from lib import mousectrl
from lib import kbdctrl
from lib import clipboardctrl
from lib.imagegrab import ImageGrab
from lib.rfb_bitmap import RfbBitmap
from lib import log


class _BandwidthEstimator:
    """Exponential moving average bandwidth estimator for adaptive rate limiting."""

    def __init__(self, alpha=0.3, window=20):
        self.alpha = alpha
        self.window = window
        self._samples = []
        self._bytes_total = 0
        self._last_time = None
        self.current_bps = 0.0

    def record_send(self, n_bytes):
        now = time.time()
        self._bytes_total += n_bytes

        if self._last_time is not None:
            dt = now - self._last_time
            if dt > 0:
                bps = n_bytes * 8 / dt
                self._samples.append(bps)
                if len(self._samples) > self.window:
                    self._samples.pop(0)
                # EMA smoothing
                self.current_bps = (
                    self.alpha * bps + (1 - self.alpha) * self.current_bps
                    if self._last_time is not None else bps
                )

        self._last_time = now

    def reset(self):
        self._samples = []
        self._bytes_total = 0
        self._last_time = None
        self.current_bps = 0.0


# encodings support
import lib.encodings as encs
from lib.encodings.common import ENCODINGS
from lib.encodings.cursor import Encoding as CursorEncoding

# auth support
from lib.auth.vnc_auth import VNCAuth
from lib.auth.vencrypt import VeNCrypt

class VNCServer():

    class RFB_SECTYPES:
        none = 1         # no authentication
        vncauth = 2      # plain VNC auth (DES challenge)
        tls = 18         # anonymous TLS + VNC auth (TigerVNC)
        apple_ard = 30   # Apple Remote Desktop (DH + AES)
        vencrypt = 19    # VeNCrypt
        unix = 129       # Unix Login Authentication

    encoding_object = None
    last_cursor = None

    def __init__(self, socket, password=None, auth_type=None, pem_file='', vnc_config = None):
        self.RFB_VERSION = '003.008'
        self.initmsg = ("RFB %s\n" % self.RFB_VERSION)
        self.socket = socket
        self.framebuffer = None
        self.password = password
        self.cursor_support = False
        self.auth_type = auth_type
        self.pem_file = pem_file
        self.vnc_config = vnc_config
        self.cursor_encoding = CursorEncoding()

        # Adaptive rate limiting: start conservative, adjust based on throughput
        try:
            fps = int(vnc_config.fps) if vnc_config else 20
        except (TypeError, ValueError, AttributeError):
            fps = 20
        self.fbupdate_min_interval = 0.02    # hard floor (50 fps max)
        self.fbupdate_max_interval = 0.20   # ceiling for slow links
        self.fbupdate_rate_limit = 1.0 / max(1, fps)
        self._bw_estimator = _BandwidthEstimator()
        self._no_cursor = bool(getattr(vnc_config, 'no_cursor', False)) if vnc_config else False
        try:
            self._scale = float(vnc_config.scale) if vnc_config else 1.0
        except (TypeError, ValueError, AttributeError):
            self._scale = 1.0

        log.debug("Configured auth type:", self.auth_type)

    def _parse_userlist(self):
        if ':' in self.password:
            default_user, default_pass = self.password.split(':', 1)
        else:
            default_user = 'user'
            default_pass = self.password

        userlist = {default_user: default_pass}

        if ';' in self.password:
            userlist = {}
            for entry in self.password.split(';'):
                if ':' in entry:
                    u, p = entry.split(':', 1)
                    userlist[u] = p
        return userlist

    def _reselect_encoding(self):
        client_has_zrle2 = 24 in self.client_encodings
        for e in encs.common.encodings_priority:
            if e not in encs.common.encodings:
                continue
            if e not in self.client_encodings:
                continue
            if not encs.common.encodings[e].enabled:
                continue
            if self.bpp <= 8 and e in (ENCODINGS.tight, ENCODINGS.zrle):
                continue
            if client_has_zrle2 and e in (ENCODINGS.tight, ENCODINGS.zrle, ENCODINGS.hextile):
                continue
            if self.encoding == e:
                return
            self.encoding = e
            log.debug("Selected %s encoding (bpp=%d)" % (encs.common.encodings[e].name, self.bpp))
            self.encoding_object = encs.common.encodings[e]()
            if hasattr(self.encoding_object, '_jpeg_quality'):
                self.encoding_object._jpeg_quality = self.vnc_config.jpeg_quality
            if hasattr(self.encoding_object, '_compression_level'):
                self.encoding_object._compression_level = self.vnc_config.compression_level
                if hasattr(self.encoding_object, '_streams'):
                    self.encoding_object._streams = [self.encoding_object._new_stream() for _ in range(4)]
                elif hasattr(self.encoding_object, '_comp'):
                    self.encoding_object._comp = self.encoding_object._new_stream()
            return

    def __del__(self):
        log.debug("VncServer died")

    def send_message(self, message):
        ''' sends a RFB message, usually an error message '''
        sock = self.socket
        message = bytes(message, 'iso8859-1')
        # 4 bytes lenght and string
        buff = pack("I%ds" % (len(message),), len(message), message)
        sock.send(buff)
    
    def get_buffer(self, timeout):
        sock = self.socket
        sock.settimeout(timeout)

        try:
            data = sock.recv(1024)
        except socket.timeout:
            data = None
            log.debug("getbuff() timeout")
        
        return data

    def init(self):
        sock = self.socket
        sock.send(self.initmsg.encode())

        # RFB version handshake
        data = self.get_buffer(30)

        log.debug("init received: '%s'" % data)
        server_version = float(self.RFB_VERSION)
        try:
            client_version = float(data[4:11])
        except Exception as e:
            log.debug(f"Error parsing client version: {str(e)}")
            return False

        log.debug("client, server:", client_version, server_version)

        # security types handshake
        # sectypes = [
        #     self.RFB_SECTYPES.vncauth,
        #     self.RFB_SECTYPES.vencrypt
        #     ]

        sectypes = [
            self.auth_type
        ]
        log.debug('sectypes', sectypes)
        sendbuff = pack("B", len(sectypes))    # number of security types
        sendbuff += pack('%sB' % len(sectypes), *sectypes)   # send available sec types
        sock.send(sendbuff)

        # get client choosen security type
        data = self.get_buffer(30)
        try:
            sectype = unpack("B", data)[0]
        except:
            sectype = None
        
        if sectype not in sectypes:
            log.debug("Incompatible security type: %s" % data)
            sock.send(pack("B", 1)) # failed handshake
            self.send_message("Incompatible security type")
            sock.close()
            return False

        log.debug("sec type data: %s" % data)

        # Working socket - may be replaced by SSL socket after TLS
        working_sock = sock

        # Parse userlist from password string
        userlist = self._parse_userlist()

        # None (type 1)
        if sectype == self.RFB_SECTYPES.none:
            from lib.auth.none_auth import NoneAuth
            if not NoneAuth().auth(sock):
                sock.close()
                return False

        # VNC Auth (type 2)
        elif sectype == self.RFB_SECTYPES.vncauth:
            auth = VNCAuth()
            auth.getbuff = self.get_buffer
            if not auth.auth(sock, self.password):
                msg = "Auth failed."
                sendbuff = pack("I", len(msg))
                sendbuff += msg.encode()
                sock.send(sendbuff)
                sock.close()
                return False

        # TLS (type 18) - anonymous TLS + VNC DES auth
        elif sectype == self.RFB_SECTYPES.tls:
            from lib.auth.tls_auth import TLSAuth
            auth = TLSAuth()
            auth.pem_file = self.pem_file
            if not auth.auth(sock, self.password):
                sock.close()
                return False
            working_sock = auth.get_socket() or sock

        # Apple ARD (type 30)
        elif sectype == self.RFB_SECTYPES.apple_ard:
            from lib.auth.apple_ard import AppleARDAuth
            auth = AppleARDAuth()
            if not auth.auth(sock, userlist):
                sock.close()
                return False

        # VeNCrypt (type 19)
        elif sectype == self.RFB_SECTYPES.vencrypt:
            auth = VeNCrypt(sock)
            auth.getbuff = self.get_buffer
            auth.pem_file = self.pem_file
            auth.send_subtypes()
            st = auth.client_subtype

            if st == VeNCrypt.SUBTYPE_TLSNONE:
                log.debug(__name__, "VeNCrypt TLSNone")
                if not auth.auth_tls_none():
                    sock.close()
                    return False
                working_sock = auth.get_socket()

            elif st == VeNCrypt.SUBTYPE_TLSVNC:
                log.debug(__name__, "VeNCrypt TLSVnc")
                if not auth.auth_tls_vnc(self.password):
                    sock.close()
                    return False
                working_sock = auth.get_socket()

            elif st in (VeNCrypt.SUBTYPE_TLSPLAIN, VeNCrypt.SUBTYPE_TLSPLAIN2):
                log.debug(__name__, "VeNCrypt TLSPlain")
                if not auth.auth_tls_plain(userlist):
                    sock.close()
                    return False
                working_sock = auth.get_socket()

            elif st == VeNCrypt.SUBTYPE_X509NONE:
                log.debug(__name__, "VeNCrypt X509None")
                if not auth.auth_x509_none():
                    sock.close()
                    return False
                working_sock = auth.get_socket()

            elif st == VeNCrypt.SUBTYPE_X509VNC:
                log.debug(__name__, "VeNCrypt X509Vnc")
                if not auth.auth_x509_vnc(self.password):
                    sock.close()
                    return False
                working_sock = auth.get_socket()

            elif st == VeNCrypt.SUBTYPE_X509PLAIN:
                log.debug(__name__, "VeNCrypt X509Plain")
                if not auth.auth_x509_plain(userlist):
                    sock.close()
                    return False
                working_sock = auth.get_socket()

            elif st == VeNCrypt.SUBTYPE_PLAIN:
                log.debug(__name__, "VeNCrypt Plain")
                if not auth.auth_plain(userlist):
                    sock.close()
                    return False

            else:
                log.debug("Unsupported VeNCrypt subtype:", st)
                sock.sendall(pack("!I", 1))
                sock.close()
                return False

            self.vencrypt_auth = auth

        # Unix Login (type 129)
        elif sectype == self.RFB_SECTYPES.unix:
            from lib.auth.unix_login import UnixLoginAuth
            auth = UnixLoginAuth()
            if not auth.auth(sock, userlist):
                sock.close()
                return False

        else:
            log.debug("Unsupported auth type")
            sock.close()
            return False

        # Replace working socket if TLS was used
        self.socket = working_sock

        # Get ClientInit
        working_sock.settimeout(30)
        data = working_sock.recv(1)
        if not data:
            log.debug("Connection closed during ClientInit")
            working_sock.close()
            return False
        # Remaining 3 bytes of padding are consumed by SetPixelFormat handler

        log.debug("Clientinit (shared flag)", repr(data))

        self.server_init()

        return True

    def server_init(self):
        # ServerInit

        sock = self.socket
        screen = ImageGrab.grab()
        if isinstance(screen, np.ndarray):
            height, width = screen.shape[:2]
        else:
            width, height = screen.size
        del screen
        if self._scale != 1.0:
            width = int(width * self._scale)
            height = int(height * self._scale)
        self.width = width
        self.height = height
        bpp = 32    # FIXME: get real bpp
        depth = 24  # FIXME: get real depth
        self.depth = depth
        self.bpp = bpp
        bigendian = 0
        self.truecolor = 1
        red_maximum = 255
        self.red_maximum = red_maximum
        green_maximum = 255
        self.green_maximum = green_maximum
        blue_maximum = 255
        self.blue_maximum = blue_maximum
        red_shift = 16
        self.red_shift = red_shift
        green_shift = 8
        self.green_shift = green_shift
        blue_shift = 0
        self.blue_shift = blue_shift
        self.rfb_bitmap = RfbBitmap()

        sendbuff =  pack("!HH", width, height)
        sendbuff += pack("!BBBB", bpp, depth, bigendian, self.truecolor)
        sendbuff += pack("!HHHBBB", red_maximum, green_maximum, blue_maximum, red_shift, green_shift, blue_shift)
        sendbuff += pack("!xxx") # padding

        desktop_name = self.vnc_config.win_title
        desktop_name_len = len(desktop_name)

        sendbuff += pack("!I", desktop_name_len)
        sendbuff += desktop_name.encode()

        log.debug("width", repr(width))
        log.debug("height", repr(height))

        sock.send(sendbuff)


    def handle_client(self):
        """Main input loop.

        Runs in the **input thread**: reads client messages and dispatches them.
        Keyboard/mouse events are processed immediately so they stay responsive
        even while a large framebuffer update is being sent in the background
        by :meth:`_fb_sender_loop`.
        """
        self.socket.settimeout(None)

        self._mouse_controller = mousectrl.MouseController()
        self._kbd_controller = kbdctrl.KeyboardController()
        self._clipboard_controller = clipboardctrl.ClipboardController()

        self.primaryOrder = "bgr"
        self.encoding = ENCODINGS.raw
        self.encoding_object = encs.common.encodings[self.encoding]()
        self.client_encodings = []

        # -- Thread coordination ------------------------------------------------
        # _sock_write_lock:  serialises socket writes between input thread
        #                     (clipboard sync) and FB sender thread.
        # _fb_event / _fb_pending:  latest-update-wins hand-off from input
        #                           thread to FB sender.
        self._sock_write_lock = threading.Lock()
        self._fb_event = threading.Event()
        self._fb_pending = None
        self._fb_pending_lock = threading.Lock()
        self._running = True

        sock = self.socket
        last_clipboard_sync = 0.0

        fb_thread = threading.Thread(
            target=self._fb_sender_loop, name='fb-sender', daemon=True
        )
        fb_thread.start()

        while self._running:
            try:
                data = sock.recv(1)
            except socket.timeout:
                continue
            except socket.error as e:
                err = e.args[0]
                if err in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
            except Exception as e:
                log.debug("exception '%s'" % e)
                break

            if not data:
                break

            # Periodic clipboard sync (server -> client)
            now = time.time()
            if now - last_clipboard_sync >= self._clipboard_controller.sync_interval:
                last_clipboard_sync = now
                try:
                    with self._sock_write_lock:
                        self._clipboard_controller.maybe_send_clipboard(sock)
                except Exception as e:
                    log.debug("Clipboard sync error: %s" % e)

            b = data[0]

            try:
                if b == 0:  # SetPixelFormat
                    fbur_data = sock.recv(19, socket.MSG_WAITALL)
                    log.debug("Client Message Type: Set Pixel Format (0)")
                    (bpp, depth, bigendian, truecolor, red_maximum,
                     green_maximum, blue_maximum,
                     red_shift, green_shift, blue_shift
                     ) = unpack("!xxxBBBBHHHBBBxxx", fbur_data)
                    log.debug("IMG bpp, depth, endian, truecolor", bpp, depth, bigendian, truecolor)
                    log.debug("SHIFTS", red_shift, green_shift, blue_shift)
                    log.debug("MAXS", red_maximum, green_maximum, blue_maximum)

                    # Hold _sock_write_lock so the FB-sender thread cannot be
                    # mid-flight in send_rectangles() while we mutate the pixel
                    # format / encoding.  Without this, RealVNC's rapid
                    # "FBUpdateRequest + SetPixelFormat" burst races with the
                    # sender and emits a FramebufferUpdate whose rect header
                    # (encoding id) and payload (encoded bytes) come from
                    # different format generations, desyncing the client.
                    with self._sock_write_lock:
                        self.bpp = bpp
                        self.depth = depth
                        self.bigendian = bigendian
                        self.truecolor = truecolor
                        self.red_maximum = red_maximum
                        self.green_maximum = green_maximum
                        self.blue_maximum = blue_maximum
                        self.red_shift = red_shift
                        self.green_shift = green_shift
                        self.blue_shift = blue_shift

                        # When red_shift > blue_shift the wire format stores blue in
                        # the low byte and red in the high byte (BGR order), so
                        # rfb_bitmap must swap channels ("bgr").
                        self.primaryOrder = "bgr" if self.red_shift > self.blue_shift else "rgb"

                        self.rfb_bitmap.bpp = self.bpp
                        self.rfb_bitmap.depth = self.depth
                        self.rfb_bitmap.dither = self.vnc_config.eightbitdither
                        self.rfb_bitmap.primaryOrder = self.primaryOrder
                        self.rfb_bitmap.truecolor = self.truecolor
                        self.rfb_bitmap.red_shift = self.red_shift
                        self.rfb_bitmap.green_shift = self.green_shift
                        self.rfb_bitmap.blue_shift = self.blue_shift
                        self.rfb_bitmap.red_maximum = self.red_maximum
                        self.rfb_bitmap.green_maximum = self.green_maximum
                        self.rfb_bitmap.blue_maximum = self.blue_maximum
                        self.rfb_bitmap.bigendian = self.bigendian

                        if self.bpp == 8:
                            self.primaryOrder = "bgr"

                        if self.client_encodings:
                            self._reselect_encoding()

                    log.debug("Using order:", self.primaryOrder)

                elif b == 2:  # SetEncoding
                    fbur_data = sock.recv(3)
                    log.debug("Client Message Type: SetEncoding (2)")
                    (nencodings,) = unpack("!xH", fbur_data)
                    log.debug("SetEncoding: total encodings", repr(nencodings))
                    fbur_data = sock.recv(4 * nencodings, socket.MSG_WAITALL)
                    new_encodings = unpack("!%si" % nencodings, fbur_data)
                    log.debug("client_encodings", repr(new_encodings), len(new_encodings))

                    # Same lock discipline as SetPixelFormat: the FB-sender
                    # thread reads self.client_encodings / self.encoding_object
                    # from inside send_rectangles(), so we must not swap them
                    # underneath it.
                    with self._sock_write_lock:
                        self.client_encodings = new_encodings
                        self.cursor_support = False
                        if not self._no_cursor and ENCODINGS.cursor in self.client_encodings:
                            log.debug("client cursor support")
                            self.cursor_encoding = CursorEncoding()
                            self.cursor_support = True

                        self._reselect_encoding()

                elif b == 3:  # FBUpdateRequest — hand off to sender thread
                    fbur_data = sock.recv(9, socket.MSG_WAITALL)
                    if not fbur_data or len(fbur_data) < 9:
                        log.debug("connection closed during FBUpdateRequest")
                        break
                    (incremental, x, y, w, h) = unpack("!BHHHH", fbur_data)
                    with self._fb_pending_lock:
                        self._fb_pending = (incremental, x, y, w, h)
                    self._fb_event.set()

                elif b == 4:  # keyboard event — process immediately
                    kbd_data = sock.recv(7, socket.MSG_WAITALL)
                    if len(kbd_data) < 7:
                        log.debug("Short keyboard read (%d bytes), closing" % len(kbd_data))
                        break
                    self._kbd_controller.process_event(kbd_data)

                elif b == 5:  # PointerEvent — process immediately
                    ptr_data = sock.recv(5, socket.MSG_WAITALL)
                    if len(ptr_data) < 5:
                        log.debug("Short pointer read (%d bytes), closing" % len(ptr_data))
                        break
                    self._mouse_controller.process_event(ptr_data)

                elif b == 6:  # ClientCutText
                    text = self._clipboard_controller.client_cut_text(sock)
                    log.debug("ClientCutText:", text)

                else:
                    fbur_data = sock.recv(4096)
                    log.debug("RAW Server received data:", repr(b), data + fbur_data)

            except Exception as e:
                log.debug("Input dispatch error (msg type %d): %s" % (b, e))

        # -- shutdown -----------------------------------------------------------
        self._running = False
        self._fb_event.set()
        fb_thread.join(timeout=3)
        self._kbd_controller.close()
        try:
            sock.close()
        except Exception:
            pass

    def _fb_sender_loop(self):
        """Framebuffer sender thread.

        Waits for FBUpdateRequest signals from the input thread, then captures
        the screen, encodes, and sends the update.  Because this runs in a
        separate thread, a slow send never blocks keyboard/mouse processing.
        """
        last_fbur = 0.0

        while self._running:
            if not self._fb_event.wait(timeout=1):
                continue
            self._fb_event.clear()

            if not self._running:
                break

            with self._fb_pending_lock:
                item = self._fb_pending
                self._fb_pending = None
            if item is None:
                continue

            incremental, x, y, w, h = item

            # Rate-limit incremental updates only
            if incremental and (time.time() - last_fbur) < self.fbupdate_rate_limit:
                remaining = self.fbupdate_rate_limit - (time.time() - last_fbur)
                if remaining > 0:
                    time.sleep(remaining)
                last_fbur = time.time()

            last_fbur = time.time()

            try:
                # Hold _sock_write_lock for the whole send so that
                # SetPixelFormat / SetEncodings (which now also take this
                # lock) cannot mutate self.bpp / self.encoding_object
                # mid-flight.  The cursor rect is built inside
                # send_rectangles() and emitted in the *same*
                # FramebufferUpdate as the framebuffer pixels, so a client
                # that swaps pixel format right after receiving the FB
                # update never gets a separate cursor message in the wrong
                # format (RealVNC's "invalid message type N" bug).
                with self._sock_write_lock:
                    self.send_rectangles(self.socket, x, y, w, h, incremental)
            except Exception as e:
                import traceback
                log.debug("FB sender error: %s" % e)
                log.debug("Traceback:\n%s" % traceback.format_exc())
                self._running = False
                break

            # Adaptive rate-limit adjustment
            sent_bytes = getattr(self, '_last_sent_bytes', 0)
            self._bw_estimator.record_send(sent_bytes)
            bps = self._bw_estimator.current_bps
            if bps > 0 and sent_bytes > 0:
                frame_budget = (bps * self.fbupdate_rate_limit) / 8
                ratio = sent_bytes / max(frame_budget, 1)
                if ratio < 0.5:
                    self.fbupdate_rate_limit = max(
                        self.fbupdate_min_interval,
                        self.fbupdate_rate_limit * 0.9
                    )
                elif ratio > 1.5:
                    self.fbupdate_rate_limit = min(
                        self.fbupdate_max_interval,
                        self.fbupdate_rate_limit * 1.1
                    )

    def get_rectangle(self, x, y, w, h):
        try:
            scr = ImageGrab.grab()
        except Exception as ex:
            log.debug("Error grabbing screen: %s" % ex)
            return False

        if isinstance(scr, np.ndarray):
            if self._scale != 1.0:
                from PIL import Image as _Img
                sh, sw = scr.shape[:2]
                img = _Img.fromarray(scr)
                img = img.resize((int(sw * self._scale), int(sh * self._scale)), _Img.LANCZOS)
                scr = np.asarray(img)
            return scr[y:y+h, x:x+w].copy()

        if scr.mode != "RGB":
            scr = scr.convert("RGB")

        if self._scale != 1.0:
            sw, sh = scr.size
            scr = scr.resize((int(sw * self._scale), int(sh * self._scale)), Image.LANCZOS)

        crop = scr.crop((x, y, x + w, y + h))
        del scr
        return crop

    def _encode_cursor_pixels(self, rgb_image):
        """Convert a PIL RGB image to wire-format pixel bytes that match the
        current framebuffer pixel format (same packing as the raw encoding).
        """
        bpp_bytes = self.bpp // 8
        arr = np.asarray(rgb_image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)
        h, w = arr.shape[:2]

        if bpp_bytes == 4:
            out = np.zeros((h, w, 4), dtype=np.uint8)
            out[:, :, :3] = arr[:, :, :3]
            return out.tobytes()
        elif bpp_bytes == 2:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            val = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out = np.zeros((h, w, 2), dtype=np.uint8)
            out[:, :, 0] = val & 0xFF
            out[:, :, 1] = (val >> 8) & 0xFF
            return out.tobytes()
        else:
            return arr.tobytes()

    def send_cursor(self, x, y):
        """Public wrapper: send the cursor as a stand-alone FramebufferUpdate.

        Kept for backwards compatibility (and for callers that need to push
        a cursor update outside the regular FB-update path).  The regular
        path now embeds the cursor inside send_rectangles() so that a
        SetPixelFormat arriving between the two cannot desync the client.
        """
        rect_bytes = self._build_cursor_rect_bytes()
        if not rect_bytes:
            return True

        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))       # FramebufferUpdate, 1 rect
        sendbuff.extend(rect_bytes)

        try:
            self.socket.sendall(sendbuff)
        except Exception as e:
            log.debug("Error sending cursor info: %s" % e)
            return False

        return True

    def _build_cursor_rect_bytes(self):
        """Build a single cursor pseudo-encoding rect (NO FramebufferUpdate
        header).  Returns b'' if the cursor is unchanged or unavailable.

        Embedding the cursor rect inside the same FramebufferUpdate as the
        framebuffer rect (see send_rectangles) is what makes the connection
        robust to SetPixelFormat: the client sees the cursor and the FB
        pixels in one atomic message, both in the *same* pixel format, so
        there is no window in which the client could be reading bytes of
        the cursor with the wrong bytes-per-pixel.
        """
        if not self.cursor_support:
            return b''

        try:
            cursor_img = self.cursor_encoding.get_cursor_image()
            if cursor_img is None:
                return b''
            if self.last_cursor == cursor_img:
                return b''

            w, h = cursor_img.size
            self.last_cursor = cursor_img

            if cursor_img.mode != "RGBA":
                cursor_img = cursor_img.convert("RGBA")

            bitmap = self.rfb_bitmap
            rgb = bitmap.get_bitmap(cursor_img)
            raw_pixels = self._encode_cursor_pixels(rgb)

            alpha = np.asarray(cursor_img)[:, :, 3]
            row_bytes = (w + 7) // 8
            pad_w = row_bytes * 8
            padded = np.zeros((h, pad_w), dtype=np.uint8)
            padded[:, :w] = (alpha > 0).astype(np.uint8)
            bitmask = np.packbits(padded, axis=1).tobytes()

            out = bytearray()
            out.extend(pack("!HHHH", 0, 0, w, h))  # hotspot (0,0), cursor size
            out.extend(pack("!i", -239))            # cursor pseudo-encoding
            out.extend(raw_pixels)
            out.extend(bitmask)
            return bytes(out)
        except Exception as e:
            import traceback
            log.debug("Cursor rect build error: %s" % e)
            log.debug("Traceback:\n%s" % traceback.format_exc())
            self.cursor_support = False
            return b''


    def send_rectangles(self, sock, x, y, w, h, incremental=0):
        # send FramebufferUpdate to client

        # Configure rfb_bitmap with the current pixel format FIRST, before
        # any pixel data is produced.  _build_cursor_rect_bytes() relies on
        # bitmap.bpp being set; if we leave it unset (or stale from a
        # previous format), get_bitmap() returns None and the cursor encode
        # crashes with "not enough values to unpack (expected 2, got 0)".
        bitmap = self.rfb_bitmap
        bitmap.bpp = self.bpp
        bitmap.depth = self.depth
        bitmap.dither = self.vnc_config.eightbitdither
        bitmap.primaryOrder = self.primaryOrder
        bitmap.truecolor = self.truecolor
        bitmap.red_shift = self.red_shift
        bitmap.green_shift = self.green_shift
        bitmap.blue_shift = self.blue_shift

        # Optionally build the cursor rect first.  Doing this BEFORE
        # anything else (and inside the same _sock_write_lock critical
        # section that the caller holds) lets us emit the cursor and the
        # framebuffer pixels as a *single* FramebufferUpdate, eliminating
        # the race where SetPixelFormat would mutate self.bpp between the
        # two separate messages (RealVNC's "invalid message type N" bug).
        cursor_rect = self._build_cursor_rect_bytes()

        # Guard against zero-dimension requests
        if w <= 0 or h <= 0:
            try:
                sock.sendall(pack("!BxH", 0, 1 if cursor_rect else 0))
                if cursor_rect:
                    sock.sendall(cursor_rect)
            except Exception:
                return False
            return

        rectangle = self.get_rectangle(x, y, w, h)
        if rectangle is None or (isinstance(rectangle, bool) and not rectangle):
            rectangle = np.zeros((h, w, 3), dtype=np.uint8)

        lastshot = rectangle
        sendbuff = bytearray()

        self.encoding_object.firstUpdateSent = False
        
        # try to send only the actual changes
        if self.framebuffer is not None and incremental == 1:
            arr_new = np.asarray(rectangle)
            arr_old = np.asarray(self.framebuffer)
            if arr_new.shape == arr_old.shape:
                mask = np.any(arr_new != arr_old, axis=2) if arr_new.ndim == 3 else (arr_new != arr_old)
                if not mask.any():
                    # No FB changes, but we may still need to ship a cursor
                    # rect that became dirty.  Emit a FramebufferUpdate with
                    # exactly the cursor rect (or zero rects if neither).
                    num = 1 if cursor_rect else 0
                    sendbuff.extend(pack("!BxH", 0, num))
                    if cursor_rect:
                        sendbuff.extend(cursor_rect)
                    sleep(0.05)
                    try:
                        sock.sendall(sendbuff)
                    except Exception as e:
                        log.debug(f"Error sending no changes: {str(e)}")
                        return False
                    return
                rows = np.any(mask, axis=1)
                cols = np.any(mask, axis=0)
                y0, y1 = np.where(rows)[0][[0, -1]]
                x0, x1 = np.where(cols)[0][[0, -1]]
                if isinstance(rectangle, np.ndarray):
                    rectangle = rectangle[int(y0):int(y1)+1, int(x0):int(x1)+1].copy()
                    h, w = rectangle.shape[:2]
                else:
                    rectangle = rectangle.crop((int(x0), int(y0), int(x1) + 1, int(y1) + 1))
                    w = rectangle.width
                    h = rectangle.height
                x, y = x + int(x0), y + int(y0)

        if self.bpp == 32 or self.bpp == 16 or self.bpp == 8:
            self.encoding_object.framebuffer = self.framebuffer
            if self.encoding == ENCODINGS.tight:
                self.encoding_object.primaryOrder = 'rgb'
                if not isinstance(rectangle, np.ndarray):
                    rectangle = np.asarray(rectangle)
                fb_bytes = self.encoding_object.send_image(x, y, w, h, rectangle, self.bpp, self.depth)
            else:
                if isinstance(rectangle, np.ndarray):
                    rectangle = Image.fromarray(rectangle, 'RGB')
                image = bitmap.get_bitmap(rectangle)
                self.encoding_object.primaryOrder = self.primaryOrder
                fb_bytes = self.encoding_object.send_image(x, y, w, h, image, self.bpp, self.depth)
        else:
            log.debug("[!] Unsupported BPP: %s" % self.bpp)
            fb_bytes = b''

        self.framebuffer = lastshot

        # send_image() returns a complete FramebufferUpdate message
        # (header + one or more rects).  We strip its 4-byte header and
        # re-emit our own so that we can append the cursor rect and bump
        # num_rects accordingly.  encodings like Tight already produce
        # multiple rects; we account for that here.
        if fb_bytes:
            num_fb_rects = unpack('!H', fb_bytes[2:4])[0]
            fb_rect_bytes = fb_bytes[4:]
        else:
            num_fb_rects = 0
            fb_rect_bytes = b''

        total_rects = num_fb_rects + (1 if cursor_rect else 0)
        sendbuff.extend(pack("!BxH", 0, total_rects))
        sendbuff.extend(fb_rect_bytes)
        if cursor_rect:
            sendbuff.extend(cursor_rect)

        # Send the entire framebuffer update in one shot.  The socket is in
        # blocking mode (settimeout(None)), so sendall blocks until the OS
        # has accepted all bytes — which is exactly what we want here.
        try:
            sock.sendall(sendbuff)
            #log.debug("send_rectangles: sent %d bytes for (%d,%d,%d,%d)" % (len(sendbuff), x, y, w, h))
        except Exception as e:
            log.debug("Error sending changes: %s" % e)
            return False

        self._last_sent_bytes = len(sendbuff)
