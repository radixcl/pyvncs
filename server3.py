#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket, select, os, sys, random, zlib
from threading import Thread
from time import sleep
from struct import *
from pyDes import *
from argparse import ArgumentParser

from pynput import mouse, keyboard

import numpy as np

from PIL import Image, ImageChops, ImageDraw, ImagePalette
if sys.platform == "linux" or sys.platform == "linux2":
    from Xlib import display, X
    # take screen images, that's not the best way, so here
    # we use directly use xlib to take the screenshot.
    class ImageGrab():
        def grab():
            dsp = display.Display()
            root = dsp.screen().root
            geom = root.get_geometry()
            w = geom.width
            h = geom.height
            raw = root.get_image(0, 0, w ,h, X.ZPixmap, 0xffffffff)
            image = Image.frombytes("RGB", (w, h), raw.data, "raw", "BGRX")
            return image

elif sys.platform == "darwin":
    import Quartz.CoreGraphics as CG
    class ImageGrab():
        def grab():
            screenshot = CG.CGWindowListCreateImage(CG.CGRectInfinite, CG.kCGWindowListOptionOnScreenOnly, CG.kCGNullWindowID, CG.kCGWindowImageDefault)
            width = CG.CGImageGetWidth(screenshot)
            height = CG.CGImageGetHeight(screenshot)
            bytesperrow = CG.CGImageGetBytesPerRow(screenshot)

            pixeldata = CG.CGDataProviderCopyData(CG.CGImageGetDataProvider(screenshot))

            i = Image.frombytes("RGBA", (width, height), pixeldata)
            (b, g, r, x) = i.split()
            i = Image.merge("RGBX", (r, g, b, x))

            return i

else:
    from PIL import ImageGrab


def selfRestart():
    try:
        p = psutil.Process(os.getpid())
        for handler in p.get_open_files() + p.connections():
            os.close(handler.fd)
    except Exception as e:
        print(e)

    python = sys.executable
    os.execl(python, python, *sys.argv)

def hexdump(data):
    str = ""
    for d in data:
        str += hex(d)
        str += "(%s) " % d
    return str

def quantizetopalette(silf, palette, dither=False):
    """Convert an RGB or L mode image to use a given P image's palette."""

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


def deflate(data, compresslevel=9, method=zlib.DEFLATED, winsize=-zlib.MAX_WBITS, memlevel=zlib.DEF_MEM_LEVEL, strategy=0):
    compress = zlib.compressobj(
            compresslevel,        # level: 0-9
            method,        # method: must be DEFLATED
            winsize,      # window size in bits:
                                  #   -15..-8: negate, suppress header
                                  #   8..15: normal
                                  #   16..30: subtract 16, gzip header
            memlevel,   # mem level: 1..8/9
            strategy                     # strategy:
                                  #   0 = Z_DEFAULT_STRATEGY
                                  #   1 = Z_FILTERED
                                  #   2 = Z_HUFFMAN_ONLY
                                  #   3 = Z_RLE
                                  #   4 = Z_FIXED
    )
    deflated = compress.compress(data)
    deflated += compress.flush()
    return deflated

def inflate(data, winsize=-zlib.MAX_WBITS):
    decompress = zlib.decompressobj(
            winsize  # see above
    )
    inflated = decompress.decompress(data)
    inflated += decompress.flush()
    return inflated

def getDictByValue(d, val):
    try:
        return [k for k,v in d.items() if v==val][0]
    except:
        return False

RFB_VERSION = '003.008'
RFB_SECTYPES = [
            2,  # VNC auth
            19  # VeNCrypt
            ]

class VncServer():

    class ENCODINGS:
        raw = 0
        zlib = -6   # disabled

    def __init__(self, socket, ip, port):
        self.initmsg = ("RFB %s\n" % RFB_VERSION)
        self.socket = socket
        self.framebuffer = None

        self.sectypes = RFB_SECTYPES

        self.BGR233 = [0, 0, 0, 0, 0, 85, 0, 0, 170, 0, 0, 255, 36, 0, 0, 36, 0, 85, 36, 0, 170, 36, 0, 255, 73, 0, 0, 73, 0, 85, 73, 0, 170, 73, 0, 255, 109, 0, 0, 109, 0, 85, 109, 0, 170, 109, 0, 255, 146, 0, 0, 146, 0, 85, 146, 0, 170, 146, 0, 255, 182, 0, 0, 182, 0, 85, 182, 0, 170, 182, 0, 255, 219, 0, 0, 219, 0, 85, 219, 0, 170, 219, 0, 255, 255, 0, 0, 255, 0, 85, 255, 0, 170, 255, 0, 255, 0, 36, 0, 0, 36, 85, 0, 36, 170, 0, 36, 255, 36, 36, 0, 36, 36, 85, 36, 36, 170, 36, 36, 255, 73, 36, 0, 73, 36, 85, 73, 36, 170, 73, 36, 255, 109, 36, 0, 109, 36, 85, 109, 36, 170, 109, 36, 255, 146, 36, 0, 146, 36, 85, 146, 36, 170, 146, 36, 255, 182, 36, 0, 182, 36, 85, 182, 36, 170, 182, 36, 255, 219, 36, 0, 219, 36, 85, 219, 36, 170, 219, 36, 255, 255, 36, 0, 255, 36, 85, 255, 36, 170, 255, 36, 255, 0, 73, 0, 0, 73, 85, 0, 73, 170, 0, 73, 255, 36, 73, 0, 36, 73, 85, 36, 73, 170, 36, 73, 255, 73, 73, 0, 73, 73, 85, 73, 73, 170, 73, 73, 255, 109, 73, 0, 109, 73, 85, 109, 73, 170, 109, 73, 255, 146, 73, 0, 146, 73, 85, 146, 73, 170, 146, 73, 255, 182, 73, 0, 182, 73, 85, 182, 73, 170, 182, 73, 255, 219, 73, 0, 219, 73, 85, 219, 73, 170, 219, 73, 255, 255, 73, 0, 255, 73, 85, 255, 73, 170, 255, 73, 255, 0, 109, 0, 0, 109, 85, 0, 109, 170, 0, 109, 255, 36, 109, 0, 36, 109, 85, 36, 109, 170, 36, 109, 255, 73, 109, 0, 73, 109, 85, 73, 109, 170, 73, 109, 255, 109, 109, 0, 109, 109, 85, 109, 109, 170, 109, 109, 255, 146, 109, 0, 146, 109, 85, 146, 109, 170, 146, 109, 255, 182, 109, 0, 182, 109, 85, 182, 109, 170, 182, 109, 255, 219, 109, 0, 219, 109, 85, 219, 109, 170, 219, 109, 255, 255, 109, 0, 255, 109, 85, 255, 109, 170, 255, 109, 255, 0, 146, 0, 0, 146, 85, 0, 146, 170, 0, 146, 255, 36, 146, 0, 36, 146, 85, 36, 146, 170, 36, 146, 255, 73, 146, 0, 73, 146, 85, 73, 146, 170, 73, 146, 255, 109, 146, 0, 109, 146, 85, 109, 146, 170, 109, 146, 255, 146, 146, 0, 146, 146, 85, 146, 146, 170, 146, 146, 255, 182, 146, 0, 182, 146, 85, 182, 146, 170, 182, 146, 255, 219, 146, 0, 219, 146, 85, 219, 146, 170, 219, 146, 255, 255, 146, 0, 255, 146, 85, 255, 146, 170, 255, 146, 255, 0, 182, 0, 0, 182, 85, 0, 182, 170, 0, 182, 255, 36, 182, 0, 36, 182, 85, 36, 182, 170, 36, 182, 255, 73, 182, 0, 73, 182, 85, 73, 182, 170, 73, 182, 255, 109, 182, 0, 109, 182, 85, 109, 182, 170, 109, 182, 255, 146, 182, 0, 146, 182, 85, 146, 182, 170, 146, 182, 255, 182, 182, 0, 182, 182, 85, 182, 182, 170, 182, 182, 255, 219, 182, 0, 219, 182, 85, 219, 182, 170, 219, 182, 255, 255, 182, 0, 255, 182, 85, 255, 182, 170, 255, 182, 255, 0, 219, 0, 20, 219, 85, 0, 219, 170, 0, 219, 255, 36, 219, 0, 36, 219, 85, 36, 219, 170, 36, 219, 255, 73, 219, 0, 73, 219, 85, 73, 219, 170, 73, 219, 255, 109, 219, 0, 109, 219, 85, 109, 219, 170, 109, 219, 255, 146, 219, 0, 146, 219, 85, 146, 219, 170, 146, 219, 255, 182, 219, 0, 182, 219, 85, 182, 219, 170, 182, 219, 255, 219, 219, 0, 219, 219, 85, 219, 219, 170, 219, 219, 255, 255, 219, 0, 255, 219, 85, 255, 219, 170, 255, 219, 255, 0, 255, 0, 0, 255, 85, 0, 255, 170, 0, 255, 255, 36, 255, 0, 36, 255, 85, 36, 255, 170, 36, 255, 255, 73, 255, 0, 73, 255, 85, 73, 255, 170, 73, 255, 255, 109, 255, 0, 109, 255, 85, 109, 255, 170, 109, 255, 255, 146, 255, 0, 146, 255, 85, 146, 255, 170, 146, 255, 255, 182, 255, 0, 182, 255, 85, 182, 255, 170, 182, 255, 255, 219, 255, 0, 219, 255, 85, 219, 255, 170, 219, 255, 255, 255, 255, 0, 255, 255, 85, 255, 255, 170, 255, 255, 255]

    def __del__(self):
        print("VncServer died")

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
            print("getbuff() timeout")
        
        return data

    def init(self):
        sock = self.socket
        sock.send(self.initmsg.encode())

        # RFB version handshake
        data = self.getbuff(30)

        print("init received: '%s'" % data)
        server_version = float(RFB_VERSION)
        try:
            client_version = float(data[4:11])
        except:
            print("Error parsing client version")
            return False

        print("client, server:", client_version, server_version)

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
            print("Incompatible security type: %s" % data)
            sock.send(pack("B", 1)) # failed handshake
            self.sendmessage("Incompatible security type")
            sock.close()
            return False

        print("sec type data: %s" % data)

        # VNC Auth
        if sectype == 2:
            # el cliente encripta el challenge con la contraseña ingresada como key
            pw = (VNC_PASSWORD + '\0' * 8)[:8]
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
                print("Auth OK")
            else:
                print("Invalid auth")
                return False

        #unsupported VNC auth type
        else:
            return False

        # get ClientInit
        data = self.getbuff(30)
        print("Clientinit (shared flag)", repr(data))

        self.ServerInit()

        return True

    def ServerInit(self):
        # ServerInit

        sock = self.socket
        screen = ImageGrab.grab()
        print("screen", repr(screen))
        size = screen.size
        print("size", repr(size))
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

        print("width", repr(width))
        print("height", repr(height))

        sock.send(sendbuff)


    def protocol(self):
        self.socket.settimeout(None)    # set nonblocking socket

        screen = ImageGrab.grab()
        size = screen.size
        width = size[0]
        height = size[1]
        del screen

        self.primaryOrder = "rgb"
        self.encoding = self.ENCODINGS.raw
        buttonmask = 0
        buttons =     [0, 0, 0, 0, 0, 0, 0, 0]
        left_pressed = 0
        right_pressed = 0
        middle_pressed = 0


        kbdmap = {
            0xff08: keyboard.Key.backspace,
            0xff09: keyboard.Key.tab,
            0xff0d: keyboard.Key.enter,
            0xff1b: keyboard.Key.esc,
            0xff63: keyboard.Key.insert if hasattr(keyboard.Key, "insert") else None,
            0xffff: keyboard.Key.delete,
            0xff50: keyboard.Key.home,
            0xff57: keyboard.Key.end,
            0xff55: keyboard.Key.page_up,
            0xff56: keyboard.Key.page_down,
            0xff51: keyboard.Key.left,
            0xff52: keyboard.Key.up,
            0xff53: keyboard.Key.right,
            0xff54: keyboard.Key.down,
            0xffbe: keyboard.Key.f1,
            0xffbf: keyboard.Key.f2,
            0xffc0: keyboard.Key.f3,
            0xffc1: keyboard.Key.f4,
            0xffc2: keyboard.Key.f5,
            0xffc3: keyboard.Key.f6,
            0xffc4: keyboard.Key.f7,
            0xffc5: keyboard.Key.f8,
            0xffc6: keyboard.Key.f9,
            0xffc7: keyboard.Key.f10,
            0xffc8: keyboard.Key.f11,
            0xffc9: keyboard.Key.f12,
            0xffca: keyboard.Key.f13,
            0xffcb: keyboard.Key.f14,
            0xffcc: keyboard.Key.f15,
            0xffcd: keyboard.Key.f16,
            0xffce: keyboard.Key.f17,
            0xffcf: keyboard.Key.f18,
            0xffd0: keyboard.Key.f19,
            0xffd1: keyboard.Key.f20,
            0xffe1: keyboard.Key.shift_l,
            0xffe2: keyboard.Key.shift_r,
            0xffe3: keyboard.Key.ctrl_l,
            0xffe4: keyboard.Key.ctrl_r,
            0xffe7: None,   # "KEY_MetaLeft"
            0xffe8: None,   # "KEY_MetaRight"
            0xffe9: keyboard.Key.cmd_l,
            0xffea: keyboard.Key.alt_gr, # "KEY_AltRight"
            0xff14: keyboard.Key.scroll_lock if hasattr(keyboard.Key, "scroll_lock") else None,
            0xff15: keyboard.Key.print_screen if hasattr(keyboard.Key, "print_screen") else None, # "KEY_Sys_Req"
            0xff7f: keyboard.Key.num_lock if hasattr(keyboard.Key, "num_lock") else None,
            0xffe5: keyboard.Key.caps_lock,
            0xff13: keyboard.Key.pause if hasattr(keyboard.Key, "pause") else None,
            0xffeb: keyboard.Key.cmd_r, # "KEY_Super_L"
            0xffec: keyboard.Key.cmd_r, # "KEY_Super_R"
            0xffed: None, # "KEY_Hyper_L"
            0xffee: None, # "KEY_Hyper_R"
            0xffb0: None, # "KEY_KP_0"
            0xffb1: None, #  "KEY_KP_1"
            0xffb2: None, #  "KEY_KP_2"
            0xffb3: None, #  "KEY_KP_3"
            0xffb4: None, #  "KEY_KP_4"
            0xffb5: None, #  "KEY_KP_5"
            0xffb6: None, #  "KEY_KP_6"
            0xffb7: None, #  "KEY_KP_7"
            0xffb8: None, #  "KEY_KP_8"
            0xffb9: None, #  "KEY_KP_9"
            0xff8d: None, #  "KEY_KP_Enter"
            0x002f: "/",  # KEY_ForwardSlash
            0x005c: "\\",  # KEY_BackSlash
            0x0020: keyboard.Key.space, # "KEY_SpaceBar"
            0xff7e: keyboard.Key.alt_gr, # altgr, at least on a mac (?)
            0xfe03: keyboard.Key.alt_l,
        }

        if sys.platform == "darwin":
            kbdmap[0xffe2] = keyboard.Key.shift
            
        while True:
            #print(".", end='', flush=True)
            r,_,_ = select.select([self.socket],[],[],0)
            if r == []:
                #no data
                sleep(0.1)
                continue

            sock = r[0]
            try:
                data = sock.recv(1) # read first byte
            except socket.timeout:
                #print("timeout")
                continue
            except Exception as e:
                print("exception '%s'" % e)
                sock.close()
                break

            if not data:
                # clinet disconnected
                sock.close()
                break

            if data[0] == 0: # client SetPixelFormat
                data2 = sock.recv(19, socket.MSG_WAITALL)
                print("Client Message Type: Set Pixel Format (0)")
                (self.bpp, self.depth, self.bigendian, self.truecolor, self.red_maximum,
                 self.green_maximum, self.blue_maximum,
                 self.red_shift, self.green_shift, self.blue_shift
                 ) = unpack("!xxxBBBBHHHBBBxxx", data2)
                print("IMG bpp, depth, endian, truecolor", self.bpp, self.depth, self.bigendian, self.truecolor)
                print("SHIFTS", self.red_shift, self.green_shift, self.blue_shift)
                print("MAXS", self.red_maximum, self.green_maximum, self.blue_maximum)

                if self.red_shift > self.blue_shift:
                    self.primaryOrder = "rgb"
                else:
                    self.primaryOrder = "bgr"
                print("Using order: ", self.primaryOrder)

                continue
            
            if data[0] == 2: # SetEncoding
                data2 = sock.recv(3)
                print("Client Message Type: SetEncoding (2)")
                (nencodings,) = unpack("!xH", data2)
                print("SetEncoding: total encodings ", repr(nencodings))
                data2 = sock.recv(4 * nencodings, socket.MSG_WAITALL)
                print("len", len(data2))
                self.client_encodings = unpack("!%si" % nencodings, data2)
                print("data", repr(self.client_encodings), len(self.client_encodings))

                if self.ENCODINGS.zlib in self.client_encodings:
                    print("Using zlib encoding")
                    self.encoding = self.ENCODINGS.zlib
                continue

            if data[0] == 3: # FBUpdateRequest
                data2 = sock.recv(9, socket.MSG_WAITALL)
                #print("Client Message Type: FBUpdateRequest (3)")
                #print(len(data2))
                (incremental, x, y, w, h) = unpack("!BHHHH", data2)
                #print("RFBU:", incremental, x, y, w, h)
                self.SendRectangles(sock, x, y, w, h, incremental)
                continue

            if data[0] == 4:
                kbdkey = ''
                data2 = sock.recv(7)
                # B = U8, L = U32
                (downflag, key) = unpack("!BxxL", data2)
                print("KeyEvent", downflag, hex(key))
                
                # special key
                if key in kbdmap:
                    kbdkey = kbdmap[key]
                else: # normal key
                    try:
                        kbdkey = keyboard.KeyCode.from_char(chr(key))
                    except:
                        kbdkey = None

                try:
                    print("KEY:", kbdkey)
                except:
                    print("KEY: (unprintable)")

                try:
                    if downflag:
                        keyboard.Controller().press(kbdkey)
                    else:
                        keyboard.Controller().release(kbdkey)
                except:
                    print("Error sending key")

                continue

            if data[0] == 5:    # PointerEvent
                data2 = sock.recv(5, socket.MSG_WAITALL)
                (buttonmask, x, y) = unpack("!BHH", data2)
                buttons[0] = buttonmask & int("0000001", 2) # left button
                buttons[1] = buttonmask & int("0000010", 2) # middle button
                buttons[2] = buttonmask & int("0000100", 2) # right button
                buttons[3] = buttonmask & int("0001000", 2) # scroll up
                buttons[4] = buttonmask & int("0010000", 2) # scroll down

                mouse.Controller().position = (x, y)

                if buttons[0] and not left_pressed:
                    print("LEFT PRESSED")
                    mouse.Controller().press(mouse.Button.left)
                    left_pressed = 1
                elif not buttons[0] and left_pressed:
                    print("LEFT RELEASED")
                    mouse.Controller().release(mouse.Button.left)
                    left_pressed = 0

                if buttons[1] and not middle_pressed:
                    print("MIDDLE PRESSED")
                    mouse.Controller().press(mouse.Button.middle)
                    middle_pressed = 1
                elif not buttons[1] and middle_pressed:
                    print("MIDDLE RELEASED")
                    mouse.Controller().release(mouse.Button.middle)
                    middle_pressed = 0

                if buttons[2] and not right_pressed:
                    print("RIGHT PRESSED")
                    mouse.Controller().press(mouse.Button.right)
                    right_pressed = 1
                elif not buttons[2] and right_pressed:
                    print("RIGHT RELEASED")
                    mouse.Controller().release(mouse.Button.right)
                    right_pressed = 0

                if buttons[3]:
                    print("SCROLLUP PRESSED")
                    mouse.Controller().scroll(0, 2)

                if buttons[4]:
                    print("SCROLLDOWN PRESSED")
                    mouse.Controller().scroll(0, -2)
                
                #print("PointerEvent", buttonmask, x, y)
                continue

            else:
                data2 = sock.recv(4096)
                print("RAW Server received data:", repr(data[0]) , data+data2)
            

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

        #print("start SendRectangles")
        rectangle = self.GetRectangle(x, y, w, h)
        if not rectangle:
            rectangle = Image.new("RGB", [w, h], (0,0,0))

        lastshot = rectangle
        sendbuff = bytearray()
        
        # try to send only the actual changes
        if self.framebuffer != None and incremental == 1:
            diff = ImageChops.difference(rectangle, self.framebuffer)
            if diff.getbbox() is None:
                rectangles = 0
                sendbuff.extend(pack("!BxH", 0, rectangles))
                sock.sendall(sendbuff)
                sleep(0.3)
                return

            if diff.getbbox() is not None:
                if hasattr(diff, "getbbox"):
                    rectangle = rectangle.crop(diff.getbbox())
                    (x, y, _, _) = diff.getbbox()
                    w = rectangle.width
                    h = rectangle.height
                    #print("XYWH:", x,y,w,h, "diff", repr(diff.getbbox()))

        stimeout = sock.gettimeout()
        sock.settimeout(None)

        if self.bpp == 32 or self.bpp == 8:
            if rectangle.mode is not "RGB":
                image = rectangle.convert("RGB")
            else:
                image = rectangle

            b = np.asarray(image)
            a = b.copy()
            del b

            if self.bpp == 32:
                redBits = 8 
                greenBits = 8 
                blueBits = 8
            elif self.bpp == 8:
                redBits = 4
                greenBits = 4
                blueBits = 4

            #redMask = ((1 << redBits) - 1) << self.red_shift
            #greenMask = ((1 << greenBits) - 1) << self.green_shift
            #blueMask = ((1 << blueBits) - 1) << self.blue_shift
            #print("redMask", redMask, greenMask, blueMask)

            if self.primaryOrder == "bgr":
                self.blue_shift = 0
                blueMask = (1 << blueBits) - 1
                self.green_shift = blueBits
                greenMask = ((1 << greenBits) - 1) << self.green_shift
                self.red_shift = self.green_shift + greenBits
                redMask = ((1 << redBits) - 1) << self.red_shift

            else:   # RGB
                self.red_shift = 0
                redMask = (1 << redBits) - 1
                self.green_shift = redBits
                greenMask = ((1 << greenBits) - 1) << self.green_shift
                self.blue_shift = self.green_shift + greenBits
                blueMask = ((1 << blueBits) - 1) << self.blue_shift

            a[..., 0] = ( a[..., 0] ) & redMask >> self.red_shift
            a[..., 1] = ( a[..., 1] ) & greenMask >> self.green_shift
            a[..., 2] = ( a[..., 2] ) & blueMask >> self.blue_shift

            image = Image.fromarray(a)
            del a

            if self.primaryOrder == "rgb":
                (b, g, r) = image.split()
                image = Image.merge("RGB", (r, g, b))
                del b,g,r
            
            if self.bpp == 32:
                image = image.convert("RGBX")
            elif self.bpp == 8:
                image = image.convert("P")
                #image = image.convert("RGB")
                #p = Image.new("P",(16,16))
                #p.putpalette(self.BGR233)
                #image = quantizetopalette(image, p, dither=CONFIG._8bitdither)
                #image.putpalette(self.BGR233)
                #del p

            #print(image)
            if self.encoding == self.ENCODINGS.zlib:
                rectangles = 1
                sendbuff.extend(pack("!BxH", 0, rectangles))
                sendbuff.extend(pack("!HHHH", x, y, w, h))
                sendbuff.extend(pack(">i", self.encoding))

                print("Compressing...")
                #zlibdata = zlib.compress( image.tobytes() )[2:-4] # no header and checksum
                #zlibdata = zlib.compress( image.tobytes() )[:-4] # no checksum
                zlibdata = zlib.compress( image.tobytes() )

                l = pack("!I", len(zlibdata) )
                print("LEN:", hexdump(l), len(zlibdata) )
                sendbuff.extend( l )        # send length
                print("ZLIB HEAD:", hexdump(zlibdata[0:2]) )
                chksum = zlibdata[-4:]
                print("ZLIB CHECKSUM:", hexdump(chksum) )
                sendbuff.extend( zlibdata ) # send compressed data

                #inf = inflate(zlibdata, winsize=-15)

                del zlibdata, l
            else:
                # send with RAW encoding
                rectangles = 1
                sendbuff.extend(pack("!BxH", 0, rectangles))
                sendbuff.extend(pack("!HHHH", x, y, w, h))
                sendbuff.extend(pack(">i", self.encoding))
                sendbuff.extend( image.tobytes() )

        elif self.bpp == -8:
            print("8BPP routines!")
            if rectangle.mode is not "RGB":
                image = rectangle.convert("RGB")
            else:
                image = rectangle

            (r, g, b) = image.split()
            image = Image.merge("RGB", (g, r, b))
            del b,g,r

            p = Image.new("P",(16,16))
            p.putpalette(self.BGR233)
            image = quantizetopalette(image, p, dither=CONFIG._8bitdither)
            image.putpalette(self.BGR233)
            del p
            rectangles = 1
            sendbuff.extend(pack("!BxH", 0, rectangles))
            sendbuff.extend(pack("!HHHH", x, y, w, h))
            sendbuff.extend(pack(">i", self.encoding))
            sendbuff.extend( image.tobytes() )

        else:
            print("[!] Unsupported BPP: %s" % self.bpp)

        self.framebuffer = lastshot
        sock.sendall(sendbuff)
        sock.settimeout(stimeout)
        #print("end SendRectangles")
        

class ControlThread(Thread):
    def __init__(self, threads):
        Thread.__init__(self)
        self.threads = threads
        self.setDaemon(True)

    def run(self):
        # elimina los threads muertos
        while True:
            sleep(1)
            for t in threads:
                if not t.isAlive():
                    print("ControlThread removing dead", t)
                    threads.remove(t)


class ClientThread(Thread):
    def __init__(self, sock, ip, port):
        Thread.__init__(self)
        self.ip = ip
        self.port = port
        self.sock = sock
        self.setDaemon(True)

    def __del__(self):
        print("ClientThread died")

    def run(self):
        print("[+] New server socket thread started for " + self.ip + ":" + str(self.port))
        #print("Thread", self)
        server = VncServer(self.sock, self.ip, self.port)
        status = server.init()

        if not status:
            print("Error negotiating client init")
            return False
        server.protocol()


def main(argv):
    global CONFIG, TCP_IP, TCP_PORT, VNC_PASSWORD, mouse, keyboard, threads, controlthread

    class CONFIG:
        _8bitdither = False

    parser = ArgumentParser()
    parser.add_argument("-l", "--listen-address", dest="TCP_IP",
                        help="Listen in this address, default: %s" % ("0.0.0.0"), required=False, default='0.0.0.0')
    parser.add_argument("-p", "--port", dest="TCP_PORT",
                        help="Listen in this port, default: %s" % ("5901"), type=int, required=False, default='5901')
    parser.add_argument("-P", "--password", help="Sets password", required=True, dest="VNC_PASSWORD")

    parser.add_argument("-8", "--8bitdither", help="Enable 8 bit dithering", required=False, action='store_true', dest="dither")

    args = parser.parse_args()
       
    # Multithreaded Python server
    TCP_IP = '0.0.0.0' if not hasattr(args,"TCP_IP") else args.TCP_IP
    TCP_PORT = '0.0.0.0' if not hasattr(args,"TCP_PORT") else args.TCP_PORT
    VNC_PASSWORD = args.VNC_PASSWORD
    CONFIG._8bitdither = args.dither

    sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockServer.bind((TCP_IP, TCP_PORT))
    
    controlthread = ControlThread(threads)
    controlthread.start()
    threads.append(controlthread)

    print("Multithreaded Python server : Waiting for connections from TCP clients...")
    print("Runing on:", sys.platform)
    while True:
        sockServer.listen(4)
        (conn, (ip,port)) = sockServer.accept()
        newthread = ClientThread(conn, ip, port)
        newthread.setDaemon(True)
        newthread.start()
        threads.append(newthread)
        #print(threads)

if __name__ == "__main__":
    try:
        threads = []
        main(sys.argv)
    except KeyboardInterrupt:
        # quit
        print("Exiting on ctrl+c...")
        #for t in threads:
        #    print("Killing", t)
        sys.exit()
