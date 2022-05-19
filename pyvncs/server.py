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
import select
import os
import sys
import random
import numpy as np

from lib import mousectrl
from lib import kbdctrl
from lib import clipboardctrl
from lib.imagegrab import ImageGrab
from lib.rfb_bitmap import RfbBitmap
from lib import log

# encodings support
import lib.encodings as encs
from lib.encodings.common import ENCODINGS

# auth support
from lib.auth.vnc_auth import VNCAuth
from lib.auth.vencrypt import VeNCrypt

class VncServer():

    class RFB_SECTYPES:
        vncauth = 2     # plain VNC auth
        vencrypt = 19   # VeNCrypt
        unix = 129      # Unix Login Authentication

    encoding_object = None

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

        log.debug("Configured auth type:", self.auth_type)


    def __del__(self):
        log.debug("VncServer died")

    def sendmessage(self, message):
        ''' sends a RFB message, usually an error message '''
        sock = self.socket
        message = bytes(message, 'iso8859-1')
        # 4 bytes lenght and string
        buff = pack("I%ds" % (len(message),), len(message), message)
        sock.send(buff)
    
    def getbuff(self, timeout):
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
        data = self.getbuff(30)

        log.debug("init received: '%s'" % data)
        server_version = float(self.RFB_VERSION)
        try:
            client_version = float(data[4:11])
        except:
            log.debug("Error parsing client version")
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
        data = self.getbuff(30)
        try:
            sectype = unpack("B", data)[0]
        except:
            sectype = None
        
        if sectype not in sectypes:
            log.debug("Incompatible security type: %s" % data)
            sock.send(pack("B", 1)) # failed handshake
            self.sendmessage("Incompatible security type")
            sock.close()
            return False

        log.debug("sec type data: %s" % data)

        # VNC Auth
        if sectype == self.RFB_SECTYPES.vncauth:
            auth = VNCAuth()
            auth.getbuff = self.getbuff
            if not auth.auth(sock, self.password):
                msg = "Auth failed."
                sendbuff = pack("I", len(msg))
                sendbuff += msg.encode()
                sock.send(sendbuff)
                sock.close()
                return False

        # VeNCrypt
        elif sectype == self.RFB_SECTYPES.vencrypt:
            userlist = {}
            try:
                userlist[self.password.split(':')[0]] = self.password.split(':')[1]
            except Exception as ex:
                log.debug("Unable to parse username:password combination.\n%s" % ex)
                sock.close()
                return False

            auth = VeNCrypt(sock)
            auth.getbuff = self.getbuff
            auth.send_subtypes()
            client_subtype = auth.client_subtype

            if client_subtype == 256: # Vencrypt Plain auth
                if not auth.auth_plain(userlist):
                    sock.close()
                    return False

            if client_subtype == 259: # Vencrypt TLSPlain auth
                auth.pem_file = self.pem_file
                auth.socket = self.socket
                if not auth.auth_tls_plain(userlist):
                    sock.close()
                    return False

            else:
                # unsupported subtype
                log.debug("Unsuported client_subtype", client_subtype)
                sock.close()
                return False
        
        #elif sectype == self.RFB_SECTYPES.unix:
        #    log.debug("UNIX!!")

        #unsupported VNC auth type
        else:
            log.debug("Unsupported auth type")
            sock.close()
            return False

        # get ClientInit
        data = self.getbuff(30)
        log.debug("Clientinit (shared flag)", repr(data))

        self.ServerInit()

        return True

    def ServerInit(self):
        # ServerInit

        sock = self.socket
        screen = ImageGrab.grab()
        log.debug("screen", repr(screen))
        size = screen.size
        log.debug("size", repr(size))
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


    def protocol(self):
        self.socket.settimeout(None)    # set nonblocking socket
        screen = ImageGrab.grab()
        size = screen.size
        width = size[0]
        height = size[1]
        del screen
        
        mousecontroller = mousectrl.MouseController()
        kbdcontroller = kbdctrl.KeyboardController()
        clipboardcontroller = clipboardctrl.ClipboardController()

        self.primaryOrder = "rgb"
        self.encoding = ENCODINGS.raw
        self.encoding_object = encs.common.encodings[self.encoding]()

        while True:
            #log.debug(".", end='', flush=True)
            r,_,_ = select.select([self.socket],[],[],0)
            if r == []:
                #no data
                sleep(0.1)
                continue

            sock = r[0]
            try:
                data = sock.recv(1) # read first byte
            except socket.timeout:
                #log.debug("timeout")
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
                data2 = sock.recv(19, socket.MSG_WAITALL)
                log.debug("Client Message Type: Set Pixel Format (0)")
                (self.bpp, self.depth, self.bigendian, self.truecolor, self.red_maximum,
                 self.green_maximum, self.blue_maximum,
                 self.red_shift, self.green_shift, self.blue_shift
                 ) = unpack("!xxxBBBBHHHBBBxxx", data2)
                log.debug("IMG bpp, depth, endian, truecolor", self.bpp, self.depth, self.bigendian, self.truecolor)
                log.debug("SHIFTS", self.red_shift, self.green_shift, self.blue_shift)
                log.debug("MAXS", self.red_maximum, self.green_maximum, self.blue_maximum)

                if self.red_shift > self.blue_shift:
                    self.primaryOrder = "rgb"
                else:
                    self.primaryOrder = "bgr"
                log.debug("Using order:", self.primaryOrder)

                continue
            
            if data[0] == 2: # SetEncoding
                data2 = sock.recv(3)
                log.debug("Client Message Type: SetEncoding (2)")
                (nencodings,) = unpack("!xH", data2)
                log.debug("SetEncoding: total encodings", repr(nencodings))
                data2 = sock.recv(4 * nencodings, socket.MSG_WAITALL)
                #log.debug("len", len(data2))
                self.client_encodings = unpack("!%si" % nencodings, data2)
                log.debug("client_encodings", repr(self.client_encodings), len(self.client_encodings))

                # cursor support?
                if ENCODINGS.cursor in self.client_encodings:
                    log.debug("client cursor support")
                    self.cursor_support = True

                # which pixel encoding to use?
                log.debug("encs.common.encodings_priority", encs.common.encodings_priority)
                for e in encs.common.encodings_priority:
                    log.debug("E", e)
                    if e in self.client_encodings:
                        if self.encoding == e:
                            # don't initialize same encoding again
                            break
                        self.encoding = e
                        log.debug("Using %s encoding" % self.encoding)
                        self.encoding_object = encs.common.encodings[self.encoding]()
                        break

                continue


            if data[0] == 3: # FBUpdateRequest
                data2 = sock.recv(9, socket.MSG_WAITALL)
                #log.debug("Client Message Type: FBUpdateRequest (3)")
                #print(len(data2))
                (incremental, x, y, w, h) = unpack("!BHHHH", data2)
                #log.debug("RFBU:", incremental, x, y, w, h)
                self.SendRectangles(sock, x, y, w, h, incremental)

                continue

            if data[0] == 4:    # keyboard event
                kbdcontroller.process_event(sock.recv(7))
                continue

            if data[0] == 5:    # PointerEvent
                mousecontroller.process_event(sock.recv(5, socket.MSG_WAITALL))
                continue
            
            if data[0] == 6:    # ClientCutText
                text = clipboardcontroller.client_cut_text(sock)
                log.debug("ClientCutText:", text)

            else:
                data2 = sock.recv(4096)
                log.debug("RAW Server received data:", repr(data[0]) , data+data2)
            

    def GetRectangle(self, x, y, w, h):
        try:
            scr = ImageGrab.grab()
        except:
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

    def SendRectangles(self, sock, x, y, w, h, incremental=0):
        # send FramebufferUpdate to client

        #log.debug("start SendRectangles")
        rectangle = self.GetRectangle(x, y, w, h)
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
                rectangles = 0
                sendbuff.extend(pack("!BxH", 0, rectangles))
                try:
                    sock.sendall(sendbuff)
                except:
                    return False
                sleep(0.1)
                return

            if diff.getbbox() is not None:
                if hasattr(diff, "getbbox"):
                    rectangle = rectangle.crop(diff.getbbox())
                    (x, y, _, _) = diff.getbbox()
                    w = rectangle.width
                    h = rectangle.height
                    #log.debug("XYWH:", x,y,w,h, "diff", repr(diff.getbbox()))

        stimeout = sock.gettimeout()
        sock.settimeout(None)

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
            sendbuff.extend(self.encoding_object.send_image(x, y, w, h, image))
        else:
            log.debug("[!] Unsupported BPP: %s" % self.bpp)

        self.framebuffer = lastshot
        try:
            sock.sendall(sendbuff)
        except:
            # connection closed?
            return False
        sock.settimeout(stimeout)
        #log.debug("end SendRectangles")
