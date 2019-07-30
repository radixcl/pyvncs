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
from lib.imagegrab import ImageGrab
from lib import log
from lib import bgr233_palette

# encodings support
import lib.encodings as encs
from lib.encodings.common import ENCODINGS

def hexdump(data):
    str = ""
    for d in data:
        str += hex(d)
        str += "(%s) " % d
    return str

def quantizetopalette(silf, palette, dither=False):
    """Converts an RGB or L mode image to use a given P image's palette."""

    silf.load()

    # use palette from reference image
    palette.load()
    if palette.mode != "P":
        raise ValueError("bad mode for palette image")
    if silf.mode != "RGB" and silf.mode != "L":
        raise ValueError(
            "only RGB or L mode images can be quantized to a palette"
            )
    im = silf.im.convert("P", 1 if dither else 0, palette.im)
    # the 0 above means turn OFF dithering

    # Later versions of Pillow (4.x) rename _makeself to _new
    try:
        return silf._new(im)
    except AttributeError:
        return silf._makeself(im)

class VncServer():

    class CONFIG:
        _8bitdither = False

    encoding_object = None

    def __init__(self, socket, password):
        self.RFB_VERSION = '003.008'
        self.RFB_SECTYPES = [
                             2,  # VNC auth
                             19  # VeNCrypt
                            ]
        self.initmsg = ("RFB %s\n" % self.RFB_VERSION)
        self.socket = socket
        self.framebuffer = None
        self.password = password
        self.sectypes = self.RFB_SECTYPES
        self.cursor_support = False
        
    def __del__(self):
        log.debug("VncServer died")

    def encrypt(self, key, data):
        k = des(key, ECB, "\0\0\0\0\0\0\0\0", pad=None, padmode=PAD_PKCS5)
        d = k.encrypt(data)
        return d

    def decrypt(self, challenge, data):
        k = des(challenge, ECB, "\0\0\0\0\0\0\0\0", pad=None, padmode=PAD_PKCS5)
        return k.decrypt(data)

    def mirrorBits(self, key):
        newkey = []
        for ki in range(len(key)):
            bsrc = key[ki]
            btgt = 0
            for i in range(8):
                if ord(bsrc) & (1 << i):
                    btgt = btgt | (1 << 7-i)
            newkey.append(btgt)
        
        return newkey

    def sendmessage(self, message):
        ''' sends a RFB message, usually an error message '''
        sock = self.socket
        message = bytes(message, 'iso8859-1')
        # 4 bytes lenght and string
        buff = pack("I%ds" % (len(message),), len(message), message)
        sock.send(message)
    
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
        sendbuff = pack("B", len(self.sectypes))    # number of security types
        sendbuff += pack('%sB' % len(self.sectypes), *self.sectypes)   # send available sec types
        sock.send(sendbuff)

        data = self.getbuff(30)
        try:
            sectype = unpack("B", data)[0]
        except:
            sectype = None
        
        if sectype not in self.sectypes:
            log.debug("Incompatible security type: %s" % data)
            sock.send(pack("B", 1)) # failed handshake
            self.sendmessage("Incompatible security type")
            sock.close()
            return False

        log.debug("sec type data: %s" % data)

        # VNC Auth
        if sectype == 2:
            # el cliente encripta el challenge con la contraseña ingresada como key
            pw = (self.password + '\0' * 8)[:8]
            challenge = os.urandom(16)  # challenge
            sock.send(challenge)    # send challenge
            # obtener desde el cliente el dato encritado
            data = self.getbuff(30)
            # la encriptacion de challenge, con pw como key debe dar data
            
            k = des(self.mirrorBits(pw))
            crypted = k.encrypt(challenge)

            if data == crypted:
                # Handshake successful
                sock.send(pack("I", 0))
                log.debug("Auth OK")
            else:
                log.debug("Invalid auth")
                return False

        #unsupported VNC auth type
        else:
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
        depth = 32  # FIXME: get real depth
        self.depth = depth
        self.bpp = bpp
        bigendian = 0
        truecolor = 1
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

        sendbuff =  pack("!HH", width, height)
        sendbuff += pack("!BBBB", bpp, depth, bigendian, truecolor)
        sendbuff += pack("!HHHBBB", red_maximum, green_maximum, blue_maximum, red_shift, green_shift, blue_shift)
        sendbuff += pack("!xxx") # padding

        desktop_name = "Test VNC"
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

            if self.bpp == 32:
                redBits = 8 
                greenBits = 8 
                blueBits = 8

                # image array
                a = np.asarray(rectangle).copy()
 
                if self.primaryOrder == "bgr":  # bit shifting
                    blueMask = (1 << blueBits) - 1
                    greenMask = ((1 << greenBits) - 1) << self.green_shift
                    redMask = ((1 << redBits) - 1) << self.red_shift

                    a[..., 0] = ( a[..., 0] ) & blueMask >> self.blue_shift
                    a[..., 1] = ( a[..., 1] ) & greenMask >> self.green_shift
                    a[..., 2] = ( a[..., 2] ) & redMask >> self.red_shift
                
                else:   # RGB
                    redMask = ((1 << redBits) - 1) << self.red_shift
                    greenMask = ((1 << greenBits) - 1) << self.green_shift
                    blueMask = ((1 << blueBits) - 1) << self.blue_shift
                    a[..., 0] = ( a[..., 0] ) & redMask >> self.red_shift
                    a[..., 1] = ( a[..., 1] ) & greenMask >> self.green_shift
                    a[..., 2] = ( a[..., 2] ) & blueMask >> self.blue_shift

                image = Image.fromarray(a)
                if self.primaryOrder == "rgb":
                    (b, g, r) = image.split()
                    image = Image.merge("RGB", (r, g, b))
                    del b,g,r
                image = image.convert("RGBX")

            if self.bpp == 16:  #BGR565
                greenBits = 5
                blueBits = 6
                redBits = 5

                image = rectangle
                if self.depth == 16:
                    image = image.convert('BGR;16')
                if self.depth == 15:
                    image = image.convert('BGR;15')

            elif self.bpp == 8: #bgr233
                redBits = 3
                greenBits = 3
                blueBits = 2

                image = rectangle

                p = Image.new('P',(16,16))
                p.putpalette(bgr233_palette.palette)

                image = quantizetopalette(image, p, dither=self.CONFIG._8bitdither)

                #image = image.convert('RGB', colors=4).quantize(palette=p)
                #log.debug(image)



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
