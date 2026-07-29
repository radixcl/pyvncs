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
        vncauth = 2     # plain VNC auth
        vencrypt = 19   # VeNCrypt
        unix = 129      # Unix Login Authentication

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
        self.fbupdate_min_interval = 0.02    # hard floor (50 fps max)
        self.fbupdate_max_interval = 0.20   # ceiling for slow links
        self.fbupdate_rate_limit = 0.05     # initial interval (20 fps)
        self._bw_estimator = _BandwidthEstimator()

        log.debug("Configured auth type:", self.auth_type)


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

        # Working socket - may be replaced by SSL socket after VeNCrypt TLS
        working_sock = sock

        # VNC Auth
        if sectype == self.RFB_SECTYPES.vncauth:
            auth = VNCAuth()
            auth.getbuff = self.get_buffer
            if not auth.auth(sock, self.password):
                msg = "Auth failed."
                sendbuff = pack("I", len(msg))
                sendbuff += msg.encode()
                sock.send(sendbuff)
                sock.close()
                return False

        # VeNCrypt
        elif sectype == self.RFB_SECTYPES.vencrypt:
            # Parse credentials: "password" for Plain, "user:pass" for TLSPlain
            if ':' in self.password:
                default_user, default_pass = self.password.split(':', 1)
            else:
                default_user = 'user'
                default_pass = self.password

            userlist = {
                default_user: default_pass
            }

            # Also parse additional users if password has multiple user:pass entries
            # separated by semicolons: "user1:pass1;user2:pass2"
            if ';' in self.password:
                userlist = {}
                for entry in self.password.split(';'):
                    if ':' in entry:
                        u, p = entry.split(':', 1)
                        userlist[u] = p

            auth = VeNCrypt(sock)
            auth.getbuff = self.get_buffer
            auth.pem_file = self.pem_file
            auth.send_subtypes()
            client_subtype = auth.client_subtype

            if client_subtype == VeNCrypt.SUBTYPE_PLAIN:
                # Plain auth without TLS encryption
                log.debug(__name__, "Using VeNCrypt Plain auth (no TLS)")
                if not auth.auth_plain(userlist):
                    sock.close()
                    return False

            elif client_subtype == VeNCrypt.SUBTYPE_TLSPLAIN:
                # TLS encrypted channel + Plain auth
                log.debug(__name__, "Using VeNCrypt TLS+Plain auth")
                if not auth.auth_tls_plain(userlist):
                    sock.close()
                    return False
                # After TLS, all communication goes through the SSL socket
                working_sock = auth.get_socket()

            else:
                # unsupported subtype
                log.debug("Unsupported client_subtype:", client_subtype)
                sendbuff = pack("!I", 1)
                working_sock.sendall(sendbuff)
                working_sock.close()
                return False

            # Store auth object for socket access
            self.vencrypt_auth = auth

        else:
            log.debug("Unsupported auth type")
            sock.close()
            return False

        # Replace working socket if VeNCrypt TLS was used
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
        #log.debug("screen", repr(screen))
        size = screen.size
        #log.debug("size", repr(size))
        del screen
        
        width = size[0]
        self.width = width
        height = size[1]
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
                    (self.bpp, self.depth, self.bigendian, self.truecolor, self.red_maximum,
                     self.green_maximum, self.blue_maximum,
                     self.red_shift, self.green_shift, self.blue_shift
                     ) = unpack("!xxxBBBBHHHBBBxxx", fbur_data)
                    log.debug("IMG bpp, depth, endian, truecolor", self.bpp, self.depth, self.bigendian, self.truecolor)
                    log.debug("SHIFTS", self.red_shift, self.green_shift, self.blue_shift)
                    log.debug("MAXS", self.red_maximum, self.green_maximum, self.blue_maximum)

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

                    log.debug("Using order:", self.primaryOrder)

                elif b == 2:  # SetEncoding
                    fbur_data = sock.recv(3)
                    log.debug("Client Message Type: SetEncoding (2)")
                    (nencodings,) = unpack("!xH", fbur_data)
                    log.debug("SetEncoding: total encodings", repr(nencodings))
                    fbur_data = sock.recv(4 * nencodings, socket.MSG_WAITALL)
                    self.client_encodings = unpack("!%si" % nencodings, fbur_data)
                    log.debug("client_encodings", repr(self.client_encodings), len(self.client_encodings))

                    self.cursor_support = False
                    if ENCODINGS.cursor in self.client_encodings:
                        log.debug("client cursor support")
                        self.cursor_encoding = CursorEncoding()
                        self.cursor_support = True

                    log.debug("encs.common.encodings_priority", encs.common.encodings_priority)
                    selected = False
                    for e in encs.common.encodings_priority:
                        if e not in encs.common.encodings:
                            continue
                        if e in self.client_encodings:
                            if self.encoding == e:
                                selected = True
                                break
                            if not encs.common.encodings[e].enabled:
                                log.debug("Encoding disabled:", e)
                                continue
                            self.encoding = e
                            log.debug("Using %s encoding" % encs.common.encodings[self.encoding].name)
                            self.encoding_object = encs.common.encodings[self.encoding]()
                            selected = True
                            break

                    if not selected:
                        log.debug("No matching encoding found, falling back to raw (0)")
                        log.debug("Client encodings:", self.client_encodings)
                        if 0 not in self.client_encodings:
                            log.error("Client does not support raw encoding (0) - connection may fail")

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
                    self._kbd_controller.process_event(
                        sock.recv(7, socket.MSG_WAITALL))

                elif b == 5:  # PointerEvent — process immediately
                    self._mouse_controller.process_event(
                        sock.recv(5, socket.MSG_WAITALL))

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
                try:
                    with self._sock_write_lock:
                        self.socket.sendall(pack("!BxH", 0, 0))
                except Exception as e:
                    log.debug("Error sending rate-limited response: %s" % e)
                    self._running = False
                    break
                continue

            last_fbur = time.time()

            try:
                with self._sock_write_lock:
                    self.send_rectangles(self.socket, x, y, w, h, incremental)
                    if self.cursor_support:
                        self.send_cursor(x, y)
            except Exception as e:
                log.debug("FB sender error: %s" % e)
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
        (scr_width, scr_height) = scr.size

        if scr.mode != "RGB":
            img = scr.convert("RGB")
        else:
            img = scr
        
        del scr

        crop = img.crop((x, y, x + w, y + h))
        del img
        
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
        cursor_img = self.cursor_encoding.get_cursor_image()
        if cursor_img is None:
            return False

        if self.last_cursor == cursor_img:
            return True

        w, h = cursor_img.size
        self.last_cursor = cursor_img

        # Ensure RGBA so we can extract the alpha mask
        if cursor_img.mode != "RGBA":
            cursor_img = cursor_img.convert("RGBA")

        # Convert to the framebuffer pixel format (correct bytes-per-pixel)
        bitmap = self.rfb_bitmap
        rgb = bitmap.get_bitmap(cursor_img)
        raw_pixels = self._encode_cursor_pixels(rgb)

        # Build transparency bitmask (1 = opaque, MSB first per row)
        bitmask = bytearray()
        row_bytes = (w + 7) // 8
        for j in range(h):
            row = 0
            for i in range(w):
                if cursor_img.getpixel((i, j))[3]:
                    row |= (128 >> (i % 8))
                if (i % 8 == 7) or i == w - 1:
                    bitmask.append(row)
                    row = 0

        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))       # FramebufferUpdate, 1 rect
        sendbuff.extend(pack("!HHHH", 0, 0, w, h)) # hotspot (0,0), cursor size
        sendbuff.extend(pack("!i", -239))           # cursor pseudo-encoding
        sendbuff.extend(raw_pixels)
        sendbuff.extend(bitmask)

        try:
            self.socket.sendall(sendbuff)
        except Exception as e:
            log.debug("Error sending cursor info: %s" % e)
            return False

        return True


    def send_rectangles(self, sock, x, y, w, h, incremental=0):
        # send FramebufferUpdate to client

        # Guard against zero-dimension requests
        if w <= 0 or h <= 0:
            try:
                sock.sendall(pack("!BxH", 0, 0))
            except Exception:
                return False
            return

        rectangle = self.get_rectangle(x, y, w, h)
        if not rectangle:
            #log.debug("send_rectangles: get_rectangle returned falsy for (%d,%d,%d,%d)" % (x, y, w, h))
            rectangle = Image.new("RGB", [w, h], (0,0,0))

        lastshot = rectangle
        sendbuff = bytearray()

        self.encoding_object.firstUpdateSent = False
        
        # try to send only the actual changes
        if self.framebuffer != None and incremental == 1:
            diff = ImageChops.difference(rectangle, self.framebuffer)
            if diff.getbbox() is None:
                # no changes...
                #log.debug("no changes")
                rectangles = 0
                sendbuff.extend(pack("!BxH", 0, rectangles))
                # clear the incoming socket buffer
                sleep(0.05)
                try:
                    sock.sendall(sendbuff)
                except Exception as e:
                    log.debug(f"Error sending no changes: {str(e)}")
                    return False
                return

            else:
                if hasattr(diff, "getbbox"):
                    #log.debug(f"RFB_REQ:", incremental, x, y, w, h)
                    rectangle = rectangle.crop(diff.getbbox())
                    (x, y, _, _) = diff.getbbox()
                    w = rectangle.width
                    h = rectangle.height
                    #log.debug(f"RFB_RES:", incremental, x, y, w, h)

        if self.bpp == 32 or self.bpp == 16 or self.bpp == 8:
            bitmap = self.rfb_bitmap
            bitmap.bpp = self.bpp
            bitmap.depth = self.depth
            bitmap.dither = self.vnc_config.eightbitdither
            bitmap.primaryOrder = self.primaryOrder
            bitmap.truecolor = self.truecolor
            bitmap.red_shift = self.red_shift
            bitmap.green_shift = self.green_shift
            bitmap.blue_shift = self.blue_shift

            image = bitmap.get_bitmap(rectangle)

            # send image with client defined encoding
            self.encoding_object.framebuffer = self.framebuffer
            sendbuff.extend(self.encoding_object.send_image(x, y, w, h, image, self.bpp, self.depth))
        else:
            log.debug("[!] Unsupported BPP: %s" % self.bpp)

        self.framebuffer = lastshot

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
