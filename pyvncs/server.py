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
        self.socket.settimeout(None)    # set nonblocking socket

        mousecontroller = mousectrl.MouseController()
        kbdcontroller = kbdctrl.KeyboardController()
        clipboardcontroller = clipboardctrl.ClipboardController()

        self.primaryOrder = "bgr"
        self.encoding = ENCODINGS.raw
        self.encoding_object = encs.common.encodings[self.encoding]()


        sock = self.socket
        last_fbur = time.time()
        last_clipboard_sync = 0.0

        while True:

            try:
                data = sock.recv(1) # read first byte
            except socket.timeout:
                #log.debug("timeout")
                continue
            except socket.error as e:
                err = e.args[0]
                # no data
                if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                    continue
            except Exception as e:
                log.debug("exception '%s'" % e)
                sock.close()
                break

            if not data:
                # clinet disconnected
                sock.close()
                break

            if data[0] == 0: # client SetPixelFormat
                fbur_data = sock.recv(19, socket.MSG_WAITALL)
                log.debug("Client Message Type: Set Pixel Format (0)")
                (self.bpp, self.depth, self.bigendian, self.truecolor, self.red_maximum,
                 self.green_maximum, self.blue_maximum,
                 self.red_shift, self.green_shift, self.blue_shift
                 ) = unpack("!xxxBBBBHHHBBBxxx", fbur_data)
                log.debug("IMG bpp, depth, endian, truecolor", self.bpp, self.depth, self.bigendian, self.truecolor)
                log.debug("SHIFTS", self.red_shift, self.green_shift, self.blue_shift)
                log.debug("MAXS", self.red_maximum, self.green_maximum, self.blue_maximum)

                # Configure primaryOrder
                self.primaryOrder = "rgb" if self.red_shift > self.blue_shift else "bgr"

                # rfb_bitmap common config
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

                # fixed bpp for 8 bpp
                if self.bpp == 8:
                    self.primaryOrder = "bgr"  # assume BGR for 8 bpp
                
                log.debug("Using order:", self.primaryOrder)

                continue
            
            if data[0] == 2: # SetEncoding
                fbur_data = sock.recv(3)
                log.debug("Client Message Type: SetEncoding (2)")
                (nencodings,) = unpack("!xH", fbur_data)
                log.debug("SetEncoding: total encodings", repr(nencodings))
                fbur_data = sock.recv(4 * nencodings, socket.MSG_WAITALL)
                #log.debug("len", len(data2))
                self.client_encodings = unpack("!%si" % nencodings, fbur_data)
                log.debug("client_encodings", repr(self.client_encodings), len(self.client_encodings))

                # cursor support?
                self.cursor_support = False
                if ENCODINGS.cursor in self.client_encodings:
                    log.debug("client cursor support")
                    self.cursor_encoding = CursorEncoding()
                    self.cursor_support = True

                # which pixel encoding to use?
                log.debug("encs.common.encodings_priority", encs.common.encodings_priority)
                selected = False
                for e in encs.common.encodings_priority:
                    if e not in encs.common.encodings:
                        continue
                    if e in self.client_encodings:
                        if self.encoding == e:
                            # don't initialize same encoding again
                            selected = True
                            break
                        # check if encoding is disabled
                        if not encs.common.encodings[e].enabled:
                            log.debug("Encoding disabled:", e)
                            continue
                        self.encoding = e
                        #log.debug("Using %s encoding" % self.encoding)
                        log.debug("Using %s encoding" % encs.common.encodings[self.encoding].name)
                        self.encoding_object = encs.common.encodings[self.encoding]()
                        selected = True
                        break

                if not selected:
                    log.debug("No matching encoding found, falling back to raw (0)")
                    log.debug("Client encodings:", self.client_encodings)
                    if 0 not in self.client_encodings:
                        log.error("Client does not support raw encoding (0) - connection may fail")

                continue


            if data[0] == 3: # FBUpdateRequest
                # rate limit (adaptive)
                fbur_data = sock.recv(9, socket.MSG_WAITALL)
                if not fbur_data:
                    log.debug("connection closed?")
                    break
                elapsed = time.time() - last_fbur
                if elapsed < self.fbupdate_rate_limit:
                    # rate limited — send empty update and let estimator run
                    try:
                        sock.sendall(pack("!BxH", 0, 0))
                    except Exception as e:
                        log.debug(f"Error sending rate limited FBUpdateRequest: {str(e)}")
                        break
                    continue

                last_fbur = time.time()
                (incremental, x, y, w, h) = unpack("!BHHHH", fbur_data)
                self.send_rectangles(sock, x, y, w, h, incremental)
                if self.cursor_support:
                    self.send_cursor(x, y)

                # Drain any input that arrived while we were sending
                try:
                    extra = sock.recv(4096)
                    if extra:
                        self._process_pending_input(sock, extra, mousecontroller, kbdcontroller, clipboardcontroller)
                except (BlockingIOError, OSError):
                    pass

                # Update bandwidth estimator with what was just sent
                self._bw_estimator.record_send(self._last_sent_bytes)
                # Adjust rate limit based on throughput
                bps = self._bw_estimator.current_bps
                if bps > 0:
                    frame_budget = (bps * self.fbupdate_rate_limit) / 8
                    # If we're well under the budget, speed up; else slow down
                    ratio = self._last_sent_bytes / max(frame_budget, 1)
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
                continue
                

            if data[0] == 4:    # keyboard event
                kbdcontroller.process_event(sock.recv(7))
                continue

            if data[0] == 5:    # PointerEvent
                x, y, _ = mousecontroller.process_event(sock.recv(5, socket.MSG_WAITALL))
                continue

            if data[0] == 6:    # ClientCutText
                text = clipboardcontroller.client_cut_text(sock)
                log.debug("ClientCutText:", text)

            else:
                fbur_data = sock.recv(4096)
                log.debug("RAW Server received data:", repr(data[0]) , data+fbur_data)

            # Periodic clipboard sync (server -> client)
            now = time.time()
            if now - last_clipboard_sync >= clipboardcontroller.sync_interval:
                last_clipboard_sync = now
                clipboardcontroller.maybe_send_clipboard(sock)

    def _process_pending_input(self, sock, data, mousecontroller, kbdcontroller, clipboardcontroller):
        """Parse and dispatch any pending RFB input messages from *data*."""
        buf = data
        while len(buf) >= 1:
            msg_type = buf[0]
            needed = 0
            if msg_type == 0:   # SetPixelFormat
                needed = 20
            elif msg_type == 2: # SetEncoding
                if len(buf) < 3:
                    break
                (nenc,) = unpack("!xH", buf[:3])
                needed = 3 + 4 * nenc
            elif msg_type == 3: # FBUpdateRequest
                needed = 9
            elif msg_type == 4: # keyboard
                needed = 7
            elif msg_type == 5: # PointerEvent
                needed = 5
            elif msg_type == 6: # ClientCutText
                if len(buf) < 4:
                    break
                (ln,) = unpack("!I", buf[1:5])
                needed = 4 + ln
            else:
                # Unknown — skip one byte to avoid infinite loop
                buf = buf[1:]
                continue

            if len(buf) < needed:
                break
            chunk = buf[:needed]
            buf = buf[needed:]

            if msg_type == 4:
                try:
                    kbdcontroller.process_event(chunk)
                except Exception:
                    pass
            elif msg_type == 5:
                try:
                    mousecontroller.process_event(chunk)
                except Exception:
                    pass
            elif msg_type == 6:
                try:
                    clipboardcontroller.client_cut_text_from_chunk(chunk)
                except Exception:
                    pass

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

        crop = img.crop((x, y, w, h))
        del img
        
        return crop

    def send_cursor(self, x, y):
        cursor_img = self.cursor_encoding.get_cursor_image()
        if cursor_img is None:
            return False

        if self.last_cursor == cursor_img:
            return True
        
        w, h = cursor_img.size
        bitmap = self.rfb_bitmap
        self.last_cursor = cursor_img
        raw_pixels = bitmap.get_bitmap(cursor_img)
        raw_pixels = raw_pixels.tobytes("raw", raw_pixels.mode)

        # Create bitmask
        bitmask = bytearray()
        for j in range(h):
            row = 0
            for i in range(w):
                if cursor_img.getpixel((i, j))[3]:  # Verify alpha pixel
                    row |= (128 >> (i % 8))
                if (i % 8 == 7) or i == w - 1:
                    bitmask.append(row)
                    row = 0

        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))  # FramebufferUpdate, 1 rectangle
        sendbuff.extend(pack("!HHHH", x, y, w, h))  # geometry
        sendbuff.extend(pack("!i", -239))  # cursor pseudo encoding
        sendbuff.extend(raw_pixels)
        sendbuff.extend(bitmask)

        try:
            self.socket.sendall(sendbuff)
        except Exception as e:
            print(f"Error sending cursor info: {e}")
            return False

        return True


    def send_rectangles(self, sock, x, y, w, h, incremental=0):
        # send FramebufferUpdate to client
        rectangle = self.get_rectangle(x, y, w, h)
        if not rectangle:
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

        # Chunked send with input draining to keep keyboard/mouse responsive
        try:
            sent = 0
            total = len(sendbuff)
            CHUNK = 65536
            while sent < total:
                chunk = sendbuff[sent:sent + CHUNK]
                n = sock.send(chunk)
                sent += n
                # Drain any pending input that arrived while we were sending
                try:
                    peek = sock.recv(4096)
                    if peek:
                        self._process_pending_input(sock, peek, mousecontroller, kbdcontroller, clipboardcontroller)
                except (BlockingIOError, OSError):
                    pass
        except Exception as e:
            log.debug(f"Error sending changes: {str(e)}")
            return False

        self._last_sent_bytes = len(sendbuff)
