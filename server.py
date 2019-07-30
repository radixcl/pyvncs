#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pyvncs
from argparse import ArgumentParser
from threading import Thread
from time import sleep
import sys
import socket
import signal
from lib import log

_debug = log.debug
#_debug = print

def signal_handler(signal, frame):
    _debug("Exiting on %s signal..." % signal)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


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
                    _debug("ControlThread removing dead", t)
                    threads.remove(t)

class ClientThread(Thread):
    def __init__(self, sock, ip, port):
        Thread.__init__(self)
        self.ip = ip
        self.port = port
        self.sock = sock
        self.setDaemon(True)

    def __del__(self):
        _debug("ClientThread died")

    def run(self):
        _debug("[+] New server socket thread started for " + self.ip + ":" + str(self.port))
        #_debug("Thread", self)
        server = pyvncs.server.VncServer(self.sock, VNC_PASSWORD)
        server.CONFIG._8bitdither = CONFIG._8bitdither
        status = server.init()

        if not status:
            _debug("Error negotiating client init")
            return False
        server.protocol()


def main(argv):
    global CONFIG, TCP_IP, TCP_PORT, VNC_PASSWORD, threads, controlthread
    class CONFIG:
        _8bitdither = False

    parser = ArgumentParser()
    parser.add_argument("-l", "--listen-address", dest="TCP_IP",
                        help="Listen in this address, default: %s" % ("0.0.0.0"), required=False, default='0.0.0.0')
    parser.add_argument("-p", "--port", dest="TCP_PORT",
                        help="Listen in this port, default: %s" % ("5901"), type=int, required=False, default='5901')
    parser.add_argument("-P", "--password", help="Sets password", required=True, dest="VNC_PASSWORD")
    parser.add_argument("-8", "--8bitdither", help="Enable 8 bit dithering", required=False, action='store_true', dest="dither")
    parser.add_argument("-O", "--output-file", help="Redirects all debug output to file", required=False, dest="OUTFILE")

    args = parser.parse_args()

    if args.OUTFILE is not None:
        fsock = open(args.OUTFILE, 'w')
        sys.stdout = sys.stderr = fsock

    # Multithreaded Python server
    TCP_IP = '0.0.0.0' if not hasattr(args,"TCP_IP") else args.TCP_IP
    TCP_PORT = '5901' if not hasattr(args,"TCP_PORT") else args.TCP_PORT
    VNC_PASSWORD = args.VNC_PASSWORD
    CONFIG._8bitdither = args.dither

    sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockServer.bind((TCP_IP, TCP_PORT))
    
    controlthread = ControlThread(threads)
    controlthread.start()
    threads.append(controlthread)

    _debug("Multithreaded Python server : Waiting for connections from TCP clients...")
    _debug("Runing on:", sys.platform)
    if sys.platform in ['win32', 'win64']:
        from lib.oshelpers import windows as win32
        if not win32.is_admin():
            ret = win32.run_as_admin()
            if ret is None:
                log.debug("Respawning with admin rights")
                sys.exit(0)
            elif ret is True:
                # admin rights
                log.debug("Running with admin rights!")
            else:
                print('Error(ret=%d): cannot elevate privilege.' % (ret))
                sys.exit(1)
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
        _debug("Exiting on ctrl+c...")
        #for t in threads:
        #    _debug("Killing", t)
        sys.exit()
 