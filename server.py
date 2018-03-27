#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket, os, sys, wx
from threading import Thread
from time import sleep
from struct import *
from pyDes import *

def hexdump(data):
    print(" ".join(hex(ord(n)) for n in data))

class VncServer():
    def __init__(self, socket, ip, port):
        self.initmsg = "RFB 003.008\n"
        self.socket = socket

        self.sectypes = [
            2,  # VNC auth
            19  # VeNCrypt
            ]

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
        if data == self.initmsg.encode():
            print("init OK")
        else:
            print("init NOT OK!! '%s' '%s'" % (data, self.initmsg.encode()))
            sock.send(pack("BB", 1, 0))
            self.sendmessage("RFB protocol not supported")
            sock.close()
            return False

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

        # ServerInit
        screen = wx.ScreenDC()
        print("screen", repr(screen))
        size = screen.GetSize()
        print("size", repr(size))
        
        width = size[0]
        height = size[1]
        bpp = 32    # FIXME: get real bpp
        depth = 24  # FIXME: get real depth
        self.depth = depth
        self.bpp = 32
        bigendian = 0
        truecolor = 1
        red_maximum = 255
        green_maximum = 255
        blue_maximum = 255
        red_shift = 16
        self.red_shift = red_shift
        green_shift = 8
        self.green_shift = green_shift
        blue_shift = 0
        self.blue_shift = blue_shift
        padding1 = 0
        padding2 = 0
        padding3 = 0

        sendbuff =  pack("!HH", width, height)
        sendbuff += pack("!BBBB", bpp, depth, bigendian, truecolor)
        sendbuff += pack("!HHHBBB", red_maximum, green_maximum, blue_maximum, red_shift, green_shift, blue_shift)
        sendbuff += pack("!BBB", padding1, padding2, padding3)

        desktop_name = "Test VNC"
        desktop_name_len = len(desktop_name)

        sendbuff += pack("!I", desktop_name_len)
        sendbuff += desktop_name.encode()

        print("width", repr(width))
        print("height", repr(height))

        sock.send(sendbuff)

        return True


    def protocol(self):
        sock = self.socket
        sock.settimeout(1)    # set nonblocking socket

        screen = wx.ScreenDC()
        size = screen.GetSize()
        width = size[0]
        height = size[1]

        while True:
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

            if data[0] == 0: # SetPixelFormat
                data2 = sock.recv(19)
                print("Client Message Type: Set Pixel Format (0)")
                (self.bpp, self.depth, self.bigendian, self.truecolor, self.red_maximum,
                 self.green_maximum, self.blue_maximum, self.red_shift, self.green_shift,
                  self.blue_shift) = unpack("!xxxBBBBHHHBBBxxx", data2)
                print("bpp", self.bpp)
                print("depth", self.depth)
            
            if data[0] == 2: # SetEncoding
                data2 = sock.recv(3)
                print("Client Message Type: SetEncoding (2)")
                h = unpack("xH", data2)
                print("h", repr(h))
                data2 = sock.recv(4096)

            else:
                data2 = sock.recv(4096)
                print("RAW Server received data:", data+data2)
            
            #sleep(0.1)
            #self.RAWSendRectangles(sock, 0, 0, width, height)

    def GetRectangle(self, x, y, w, h):
        screen = wx.ScreenDC()
        size = screen.GetSize()
        bmp = wx.Bitmap(size[0], size[1], self.depth)
        mem = wx.MemoryDC()
        mem.SelectObject(bmp)
        mem.Blit(0, 0, size[0], size[1], screen, 0, 0)
        mem.SelectObject(wx.NullBitmap)

        rect = bmp.GetSubBitmap(wx.Rect(x, y, w, h))
        image = wx.Bitmap.ConvertToImage(rect)

        del mem
        del bmp
        del rect

        return image

    def RAWSendRectangles(self, sock, x, y, w, h):
        image = self.GetRectangle(x, y, w, h)        
        pixelData = image.GetData()

        sendbuff  = pack("!BBH", 0, 0, 1)
        sendbuff += pack("!HHHH", x, y, w, h)
        sendbuff += pack("!i", 0)
        sock.send(sendbuff)

        sendbuff = bytearray()
        print("Start...")
        for b in range(0, len(pixelData), 3):
            red = (pixelData[b] & 0x000000FF) >> self.red_shift
            green = (pixelData[b+1] & 0x000000FF) >> self.green_shift
            blue = (pixelData[b+2] & 0x000000FF) >> self.blue_shift
            sendbuff.extend( pack("!BBBx", pixelData[b], pixelData[b+1], pixelData[b+2]) )
            #sendbuff += pack("BBBx", pixelData[b], pixelData[b+1], pixelData[b+2])
            #print(".", end='', flush=True)

        print("End...")
        sock.sendall(sendbuff)

        #c = 0
        #for d in pixelData:
        #    if c == 3:
        #        c=0
        #        sock.send( pack("x") )

        #    sock.send( pack("B", d))
        #    c += 1

        #sock.sendall(pack( "B" , pixelData))
        #print("Supossed data len:", w * h * self.depth)
        #print("Real data len:    ", len(pixelData))
        

class ControlThread(Thread):
    def __init__(self, threads):
        Thread.__init__(self)
        self.threads = threads

    def run(self):
        # elimina los threads muertos
        while True:
            sleep(1)
            for t in threads:
                if not t.isAlive():
                    threads.remove(t)


class ClientThread(Thread):
    def __init__(self, sock, ip, port):
        Thread.__init__(self)
        self.ip = ip
        self.port = port
        self.sock = sock

    def __del__(self):
        print("ClientThread died")

    def run(self):
        print("[+] New server socket thread started for " + self.ip + ":" + str(self.port))
        server = VncServer(self.sock, self.ip, self.port)
        status = server.init()
        if not status:
            print("Error negotiating client init")
            return False
        server.protocol()


# Multithreaded Python server
TCP_IP = '0.0.0.0'
TCP_PORT = 5901

VNC_PASSWORD='kaka80'

app = wx.App(False)

sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sockServer.bind((TCP_IP, TCP_PORT))
threads = []

controlthread = ControlThread(threads)
controlthread.start()

print("Multithreaded Python server : Waiting for connections from TCP clients...")
while True:
    sockServer.listen(4)
    (conn, (ip,port)) = sockServer.accept()
    newthread = ClientThread(conn, ip, port)
    newthread.setDaemon(True)
    newthread.start()
    threads.append(newthread)
    #print(threads)
