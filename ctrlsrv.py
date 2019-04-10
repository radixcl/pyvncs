#!/usr/bin/env python3
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

import pyvncs
from lib import log
from lib import common
from argparse import ArgumentParser
from threading import Thread
import time
import sys
import socket
import signal
import readline
import traceback

#_debug = log.debug
_debug = print

if common.isWindows():
    _debug("Wintendo...")
    import win32ts
    import win32security
    import win32con
    import win32api
    import ntsecuritycon
    import win32process
    import win32event

def signal_handler(signal, frame):
    _debug("Exiting on signal %s..." % signal)
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

config = {
}

class ControlThread(Thread):
    def __init__(self, threads):
        Thread.__init__(self)
        self.threads = threads
        self.setDaemon(True)

    def run(self):
        # elimina los threads muertos
        while True:
            time.sleep(1)
            for t in threads:
                if not t.isAlive():
                    _debug("ControlThread removing dead", t)
                    threads.remove(t)

class VNCThread(Thread):
    def __init__(self, port, password):
        Thread.__init__(self)
        self.ip = None
        self.port = port
        self.sock = None
        self.password = password
        self.setDaemon(True)

    def __del__(self):
        _debug("VNCThread died")
    

    def run(self):

        TCP_IP = '0.0.0.0'
        _debug("[+] Listen...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((TCP_IP, int(self.port)))
        self.sock.listen(4)
        (conn, (ip,port)) = self.sock.accept()

        _debug("[+] New server socket started for " + ip + ":" + str(port))
        #_debug("Thread", self)
        server = pyvncs.server.VncServer(conn, self.password)
        #server.CONFIG._8bitdither = CONFIG._8bitdither
        status = server.init()

        if not status:
            _debug("Error negotiating client init")
            return False
        server.protocol()


class ClientThread(Thread):
    def __init__(self, sock, ip, port, config):
        Thread.__init__(self)
        self.ip = ip
        self.port = port
        self.sock = sock
        self.setDaemon(True)
        self.config = config

    def __del__(self):
        _debug("ClientThread died")

    def run(self):
        #_debug("[+] New server socket thread started for " + self.ip + ":" + str(self.port))
        #_debug("Thread", self)
        f = self.sock.makefile('rw')

        f.write("AUTH:>")
        f.flush()
        passwd = f.readline().strip("\n")
        if passwd != config["PASSWORD"]:
            time.sleep(1)
            f.write("!NO AUTH")
            f.flush()
            _debug("NO AUTH '%s' != '%s'" % (passwd, config["PASSWORD"]))
            self.sock.close()
            return

        while True:
            f.write("OK:>")
            f.flush()
            try:
                data = f.readline()
                cmd = data.strip()
                if not data: break
                
                if cmd == "_DEBUG":
                    sys.stdout = sys.stderr = f
                    f.write("OK\n")

                elif cmd == "PING":
                    f.write("PONG\n")

                elif cmd == "QUIT":
                    f.write("BYE\n")
                    self.sock.close()
                    return

                elif cmd.startswith("STARTVNC"):
                    params = cmd.split()
                    if len(params) != 3:
                        f.write("!NOT_PARAMS\n")
                        f.flush()
                        continue
                    _debug("START VNC !!!")
                    newthread = VNCThread(params[1], params[2])
                    newthread.setDaemon(True)
                    newthread.start()

                elif cmd == "_WINSESSIONS" and common.isWindows():
                    winsessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
                    print(winsessions, file=f)

                elif cmd == "_WINCONSOLE" and common.isWindows():
                    winsessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
                    print(winsessions, file=f)
                    active = win32ts.WTSGetActiveConsoleSessionId()
                    print(active, file=f)
                    token = win32ts.WTSQueryUserToken(active)
                    print(token, file=f)
                    duplicated = win32security.DuplicateTokenEx(token, win32con.MAXIMUM_ALLOWED, win32con.NULL, win32security.TokenPrimary, win32security.SECURITY_ATTRIBUTES())
                    print("duplicated", token, file=f)
                
                elif cmd == "_TEST" and common.isWindows():
                    winsessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
                    print(winsessions, file=f)
                    active = win32ts.WTSGetActiveConsoleSessionId()
                    print(active, file=f)
                    token = win32ts.WTSQueryUserToken(active)
                    print("token", token, file=f)
                    ntoken = win32security.DuplicateTokenEx(token, 3 , win32con.MAXIMUM_ALLOWED , win32security.TokenPrimary , win32security.SECURITY_ATTRIBUTES() )
                    print("ntoken", ntoken, file=f)
                    #th = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.MAXIMUM_ALLOWED)
                    #print("th", th, file=f)
                    shell_as2(ntoken, True, "cmd")

                elif cmd == "_EVAL":
                    while True:
                        f.write("EV:>")
                        f.flush()
                        data = f.readline()
                        cmd = data.strip()
                        if cmd == "": continue
                        if cmd == "_QUIT": break
                        if not data: break

                        try:
                            eval(data)
                        except:
                            print("ERROR:", sys.exc_info()[0], file=f)
                            print(traceback.format_exc(), file=f)
                            f.flush()

                    _debug("eval:", cmd.strip())
                    f.flush()

                elif cmd == "_ERROR":
                    a = 1/0
                
                else:
                    f.write("!NO_CMD %s\n" % cmd)

                _debug("command:", cmd.strip())
                f.flush()
            except:
                print("ERROR:", sys.exc_info()[0], file=f)
                print(traceback.format_exc(), file=f)
                f.flush()

def get_all_privs(th):
    # Try to give ourselves some extra privs (only works if we're admin):
    # SeBackupPrivilege   - so we can read anything
    # SeDebugPrivilege    - so we can find out about other processes (otherwise OpenProcess will fail for some)
    # SeSecurityPrivilege - ??? what does this do?

    # Problem: Vista+ support "Protected" processes, e.g. audiodg.exe.  We can't see info about these.
    # Interesting post on why Protected Process aren't really secure anyway: http://www.alex-ionescu.com/?p=34

    privs = win32security.GetTokenInformation(th, ntsecuritycon.TokenPrivileges)
    for privtuple in privs:
        privs2 = win32security.GetTokenInformation(th, ntsecuritycon.TokenPrivileges)
        newprivs = []
        for privtuple2 in privs2:	
            if privtuple2[0] == privtuple[0]:
                newprivs.append((privtuple2[0], 2))  # SE_PRIVILEGE_ENABLED
            else:
                newprivs.append((privtuple2[0], privtuple2[1]))

        # Adjust privs
        privs3 = tuple(newprivs)
        win32security.AdjustTokenPrivileges(th, False, privs3)
def shell_as(th, enable_privs = 0):
    #t = thread(th)
    #print(t.as_text())
    new_tokenh = win32security.DuplicateTokenEx(th, 3 , win32con.MAXIMUM_ALLOWED , win32security.TokenPrimary , win32security.SECURITY_ATTRIBUTES() )
    print("new_tokenh: %s" % new_tokenh)
    print("Impersonating...")
    if enable_privs:
        get_all_privs(new_tokenh) 
    commandLine = "cmd"
    si = win32process.STARTUPINFO()
    print("pysecdump: Starting shell with required privileges...")
    (hProcess, hThread, dwProcessId, dwThreadId) = win32process.CreateProcessAsUser(
                            new_tokenh,
                            None, # AppName
                            commandLine, # Command line
                            None, # Process Security
                            None, # ThreadSecurity
                            1, # Inherit Handles?
                            win32process.NORMAL_PRIORITY_CLASS,
                            None, # New environment
                            None, # Current directory
                            si) # startup info.
    win32event.WaitForSingleObject( hProcess, win32event.INFINITE );
    print("pysecdump: Quitting")

def shell_as2(new_tokenh, enable_privs = 0, commandLine = "cmd"):
    print("new_tokenh: %s" % new_tokenh)
    print("Impersonating...")
    if enable_privs:
        get_all_privs(new_tokenh) 
    si = win32process.STARTUPINFO()
    print("pysecdump: Starting shell with required privileges...")
    (hProcess, hThread, dwProcessId, dwThreadId) = win32process.CreateProcessAsUser(
                            new_tokenh,
                            None, # AppName
                            commandLine, # Command line
                            None, # Process Security
                            None, # ThreadSecurity
                            1, # Inherit Handles?
                            win32process.NORMAL_PRIORITY_CLASS,
                            None, # New environment
                            None, # Current directory
                            si) # startup info.
    win32event.WaitForSingleObject( hProcess, win32event.INFINITE )
    print("pysecdump: Quitting")

def main(argv):
    global threads, config
    parser = ArgumentParser()
    parser.add_argument("-l", "--listen-address", dest="TCP_IP",
                        help="Listen in this address, default: %s" % ("0.0.0.0"), required=False, default='0.0.0.0')
    parser.add_argument("-p", "--port", dest="TCP_PORT",
                        help="Listen in this port, default: %s" % ("5899"), type=int, required=False, default='5899')
    parser.add_argument("-P", "--password", help="Sets password", required=True, dest="PASSWORD")

    args = parser.parse_args()

    config["PASSWORD"] = args.PASSWORD
    config["PORT"] = args.TCP_PORT

    sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockServer.bind((args.TCP_IP, args.TCP_PORT))

    controlthread = ControlThread(threads)
    controlthread.start()
    threads.append(controlthread)

    #_debug("Multithreaded Python server : Waiting for connections from TCP clients...")
    _debug("Runing on:", sys.platform)
    while True:
        sockServer.listen(4)
        (conn, (ip,port)) = sockServer.accept()
        newthread = ClientThread(conn, ip, port, config)
        newthread.setDaemon(True)
        newthread.start()
        threads.append(newthread)
        #print(threads)



if __name__ == "__main__":
    threads = []
    main(sys.argv)
