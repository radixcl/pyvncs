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

import sys
from multiprocessing import Process, Value
import subprocess
import os
import time
import mmap
import psutil

def isWindows():
    return sys.platform.startswith("win")

def isOSX():
    return sys.platform.startswith("darwin")

def isLinux():
    return sys.platform.startswith("linux")

class proc:
    def __init__(self):
        self.pid = Value('i', 0)
        self._process = None
    
    def __del__(self):
        pass

    def _setpid(self, pid):
        self.pid.value = pid
    
    def getpid(self):
        return self.pid.value

    def _newproc(self, cmd):
        pr = subprocess.Popen(cmd)
        #print("Launched forkproc Process ID:", str(pr.pid))
        self._setpid(pr.pid)
    
    def run(self, *cmd):
        self._process = Process(target=self._newproc, args=(cmd))
        self._process.start()
        self._process.join()
        return self.pid.value
    
    def terminate(self):
        if psutil.pid_exists(self.pid.value):
            p = psutil.Process(self.pid.value)
            p.terminate()
        self._process.terminate()
    
    def waitproc(self):
        while psutil.pid_exists(self.pid):
            time.sleep(.25)

def reshape(a, cols):
    
    for i in range(0, int(len(a)/cols), cols):
        print(i)
        print(a[i:i+cols])
