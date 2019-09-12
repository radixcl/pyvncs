#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pyvncs
from argparse import ArgumentParser
from threading import Thread
from time import sleep
import sys
import socket
import ssl
import signal
from lib import log

_debug = log.debug

def signal_handler(signal, frame):
    _debug("Exiting on %s signal..." % signal)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

class ClientThread(Thread):
    def __init__(self, sock, ip, port, vnc_config):
        Thread.__init__(self)
        self.ip = ip
        self.port = port
        self.sock = sock
        self.setDaemon(True)
        self.vnc_config = vnc_config


    def __del__(self):
        _debug("ClientThread died")

    def run(self):
        _debug("[+] New server socket thread started for " + self.ip + ":" + str(self.port))
        server = pyvncs.server.VncServer(self.sock,
                                        auth_type=self.vnc_config.auth_type,
                                        password=self.vnc_config.vnc_password,
                                        pem_file=self.vnc_config.pem_file,
                                        vnc_config=self.vnc_config
                                        )
        #server.vnc_config.eightbitdither = self.vnc_config.eightbitdither
        status = server.init()

        if not status:
            _debug("Error negotiating client init")
            return False
        
        server.protocol()


def main(argv):
    class vnc_config:
        pass

    parser = ArgumentParser()
    parser.add_argument("-l", "--listen-address", dest="listen_addr",
                        help="Listen in this address, default: %s" % ("0.0.0.0"), required=False, default='0.0.0.0')
    parser.add_argument("-p", "--port", dest="listen_port",
                        help="Listen in this port, default: %s" % ("5901"), type=int, required=False, default='5901')
    parser.add_argument("-A", "--auth-type",
                        help="Sets VNC authentication type (supported: 2(vnc), 19(vencrypt))",
                        required=False,
                        type=int,
                        default=2,
                        dest="auth_type"
                        )
    parser.add_argument("-C", "--cert-file",
                        help="SSL PEM file",
                        required=False,
                        type=str,
                        default='',
                        dest='pem_file'
                        )
    parser.add_argument("-P", "--vncpassword", help="Sets authentication password", required=True, dest="vnc_password")
    parser.add_argument("-8", "--8bitdither", help="Enable 8 bit dithering", required=False, action='store_true', dest="dither")
    parser.add_argument("-O", "--output-file", help="Redirects all debug output to file", required=False, dest="outfile")
    parser.add_argument("-t", "--title", help="VNC Window title", required=False, dest="win_title", default="pyvncs")

    args = parser.parse_args()

    if args.outfile is not None:
        try:
            fsock = open(args.outfile, 'w')
        except Exception as ex:
            print("Error:", ex, file=sys.stderr)
            sys.exit(1)
        sys.stdout = sys.stderr = fsock

    vnc_config.vnc_password = args.vnc_password
    vnc_config.eightbitdither = args.dither
    vnc_config.auth_type = args.auth_type
    vnc_config.pem_file = args.pem_file
    vnc_config.win_title = args.win_title

    sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockServer.bind((args.listen_addr, args.listen_port))
    
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
        newthread = ClientThread(sock=conn, ip=ip, port=port, vnc_config=vnc_config)
        newthread.setDaemon(True)
        newthread.start()


if __name__ == "__main__":
    try:
        main(sys.argv)
    except KeyboardInterrupt:
        # quit
        _debug("Exiting on ctrl+c...")
        sys.exit()
 