#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from argparse import ArgumentParser
from threading import Thread
from time import sleep
import sys
import os
import socket
import ssl
import signal
from lib import log
import pyvncs

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
        self.daemon = True
        self.vnc_config = vnc_config

    def __del__(self):
        _debug("ClientThread died")

    def run(self):
        _debug("[+] New server socket thread started for " + self.ip + ":" + str(self.port))
        server = None
        try:
            server = pyvncs.server.VNCServer(self.sock,
                                            auth_type=self.vnc_config.auth_type,
                                            password=self.vnc_config.vnc_password,
                                            pem_file=self.vnc_config.pem_file,
                                            vnc_config=self.vnc_config
                                            )
            status = server.init()

            if not status:
                _debug("Error negotiating client init")
                return

            server.handle_client()

        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            _debug("ClientThread exception: %s" % e)
        finally:
            try:
                self.sock.close()
            except Exception:
                pass


def main(argv):
    class vnc_config:
        pass

    parser = ArgumentParser(description="PyVNCs - Multiplatform Python VNC Server")
    parser.add_argument("-l", "--listen-address", dest="listen_addr",
                        help="Listen in this address, default: 0.0.0.0", required=False, default='0.0.0.0')
    parser.add_argument("-p", "--port", dest="listen_port",
                        help="Listen on this port, default: 5901", required=False, type=int, default=5901)
    parser.add_argument("-A", "--auth-type",
                        help="Sets VNC authentication type "
                             "(1=None, 2=VNC, 18=TLS, 19=VeNCrypt, 30=AppleARD, 129=UnixLogin). Default: 2",
                        required=False,
                        type=int,
                        default=2,
                        dest="auth_type"
                        )
    parser.add_argument("-u", "--username",
                        help="Sets the VNC username (for VeNCrypt auth). Default: user",
                        required=False,
                        type=str,
                        default='',
                        dest="vnc_username"
                        )
    parser.add_argument("-U", "--users-file",
                        help="File with multiple users (one user:pass per line) for VeNCrypt",
                        required=False,
                        type=str,
                        default='',
                        dest="users_file"
                        )
    parser.add_argument("-P", "--vncpassword",
                        help="Sets VNC password. For VeNCrypt: user:pass or user:pass;user2:pass2",
                        required=False, dest="vnc_password")
    parser.add_argument("-C", "--cert-file",
                        help="SSL PEM certificate file for VeNCrypt TLS. Auto-generated if missing.",
                        required=False,
                        type=str,
                        default='',
                        dest='pem_file'
                        )
    parser.add_argument("-8", "--8bitdither",
                        help="Enable 8-bit color dithering",
                        required=False,
                        action='store_true',
                        dest="dither")
    parser.add_argument("-O", "--output-file",
                        help="Redirects all debug output to file",
                        required=False, dest="outfile")
    parser.add_argument("-t", "--title",
                        help="VNC Window title",
                        required=False, dest="win_title",
                        default="pyvncs")
    parser.add_argument("-E", "--disable-encodings",
                        help="Comma-separated list of encodings to disable "
                             "(raw, hextile, tight, zlib, zrle). Example: -E hextile,zlib",
                        required=False, dest="disabled_encodings",
                        default='')
    parser.add_argument("-e", "--only-encodings",
                        help="Comma-separated whitelist of encodings to allow "
                             "(raw, hextile, tight, zlib, zrle). Example: -e tight,raw",
                        required=False, dest="only_encodings",
                        default='')
    parser.add_argument("-z", "--compression",
                        help="Zlib compression level 0-9 (0=fastest, 9=smallest). Default: 1",
                        required=False, type=int, default=1,
                        dest="compression_level")
    parser.add_argument("-q", "--jpeg-quality",
                        help="JPEG quality 1-100 for tight encoding. Default: 50",
                        required=False, type=int, default=50,
                        dest="jpeg_quality")
    parser.add_argument("-f", "--fps",
                        help="Maximum frames per second. Default: 20",
                        required=False, type=int, default=20,
                        dest="fps")
    parser.add_argument("-L", "--log-level",
                        help="Log level (debug, info, warning, error). Default: debug",
                        required=False, default='debug',
                        dest="log_level")
    parser.add_argument("--no-cursor",
                        help="Disable cursor pseudo-encoding",
                        required=False, action='store_true',
                        dest="no_cursor")
    parser.add_argument("-s", "--scale",
                        help="Scale factor for framebuffer (e.g. 0.5 for half resolution)",
                        required=False, type=float, default=1.0,
                        dest="scale")
    parser.add_argument("--list-encodings",
                        help="List available encodings and exit",
                        action='store_true', dest="list_encodings")

    args = parser.parse_args()

    if args.list_encodings:
        import lib.encodings
        from lib.encodings.common import encodings
        print("Available encodings (for use with -e / -E):")
        for eid in sorted(encodings, key=lambda k: encodings[k].id):
            cls = encodings[eid]
            if getattr(cls, 'pseudoEncoding', False) or cls.id < 0:
                continue
            print("  %-10s %s (id=%d)" % (cls.name, cls.description, cls.id))
        sys.exit(0)

    if not args.vnc_password:
        parser.error("-P/--vncpassword is required")

    # Configure log level before anything else
    log.set_level(args.log_level)

    # Filter encodings based on CLI args
    from lib.encodings.common import encodings, encodings_priority
    if args.only_encodings:
        allowed = [e.strip() for e in args.only_encodings.lower().split(',') if e.strip()]
        for eid in list(encodings):
            cls = encodings[eid]
            if cls.name.lower() not in allowed and eid != 0:
                del encodings[eid]
        encodings_priority[:] = [e for e in encodings_priority if e in encodings]
    elif args.disabled_encodings:
        disabled = [e.strip() for e in args.disabled_encodings.lower().split(',') if e.strip()]
        for eid in list(encodings):
            cls = encodings[eid]
            if cls.name.lower() in disabled and eid != 0:
                del encodings[eid]
        encodings_priority[:] = [e for e in encodings_priority if e in encodings]

    if args.outfile is not None:
        try:
            fsock = open(args.outfile, 'w')
        except Exception as ex:
            print("Error:", ex, file=sys.stderr)
            sys.exit(1)
        sys.stdout = sys.stderr = fsock

    # Build password string
    password = args.vnc_password

    # If username provided separately, prepend it
    if args.vnc_username and ':' not in args.vnc_password:
        password = args.vnc_username + ':' + args.vnc_password

    # Load users from file if specified
    if args.users_file:
        try:
            users = []
            with open(args.users_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and ':' in line:
                        users.append(line)
            if users:
                password = ';'.join(users)
        except Exception as ex:
            print("Error loading users file:", ex, file=sys.stderr)
            sys.exit(1)

    vnc_config.vnc_password = password
    vnc_config.eightbitdither = args.dither
    vnc_config.auth_type = args.auth_type
    vnc_config.pem_file = args.pem_file
    vnc_config.win_title = args.win_title
    vnc_config.compression_level = max(0, min(9, args.compression_level))
    vnc_config.jpeg_quality = max(1, min(100, args.jpeg_quality))
    vnc_config.fps = max(1, min(120, args.fps))
    vnc_config.no_cursor = args.no_cursor
    vnc_config.scale = args.scale

    sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockServer.bind((args.listen_addr, args.listen_port))
    
    auth_name = "VeNCrypt" if args.auth_type == 19 else "VNC"
    _debug("Starting PyVNCs with %s authentication" % auth_name)
    _debug("Multithreaded Python server : Waiting for connections on %s:%d..." % (args.listen_addr, args.listen_port))
    _debug("Running on:", sys.platform)
    # FIXME run_as_admin() is not working on windows
    # if sys.platform in ['win32', 'win64']:
    #     from lib.oshelpers import windows as win32
    #     if not win32.is_admin():
    #         ret = win32.run_as_admin()
    #         if ret is None:
    #             log.debug("Respawning with admin rights")
    #             sys.exit(0)
    #         elif ret is True:
    #             # admin rights
    #             log.debug("Running with admin rights!")
    #         else:
    #             print('Error(ret=%d): cannot elevate privilege.' % (ret))
    #             sys.exit(1)
    while True:
        sockServer.listen(4)
        (conn, (ip,port)) = sockServer.accept()
        newthread = ClientThread(sock=conn, ip=ip, port=port, vnc_config=vnc_config)
        newthread.start()


if __name__ == "__main__":
    try:
        main(sys.argv)
    except KeyboardInterrupt:
        _debug("Exiting on ctrl+c...")
        sys.exit(0)
