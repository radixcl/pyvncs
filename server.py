#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pyvncs
from argparse import ArgumentParser
from threading import Thread
from time import sleep

import sys
import socket


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
        server = pyvncs.server.VncServer(self.sock, VNC_PASSWORD)
        server.CONFIG._8bitdither = CONFIG._8bitdither
        status = server.init()

        if not status:
            print("Error negotiating client init")
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
 