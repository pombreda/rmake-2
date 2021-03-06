#
# Copyright (c) SAS Institute Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import asyncore
import os
import socket

from conary.lib import util

from rmake.lib import procutil
from rmake.lib import server

class BuildLogRecorder(asyncore.dispatcher, server.Server):
    def __init__(self, key=None):
        server.Server.__init__(self)
        self.host = procutil.getNetName()
        self.port = None
        self.logPath = None
        self.logFd = None
        self.key = key

    def _exit(self, rc=0):
        return os._exit(rc)

    def closeOtherFds(self):
        for fd in range(3,256):
            if fd not in (self.logFd, self._fileno):
                try:
                    os.close(fd)
                except OSError, e:
                    pass

    def attach(self, trove, map=None):
        asyncore.dispatcher.__init__(self, None, map)
        self.trove = trove
        self.openSocket()
        self.openLogFile()

    def handleRequestIfReady(self, sleepTime=0.1):
        asyncore.poll2(timeout=sleepTime, map=self._map)

    def getPort(self):
        return self.port

    def getHost(self):
        return self.host

    def getLogPath(self):
        return self.logPath

    def openLogFile(self):
        util.mkdirChain(os.path.dirname(self.trove.logPath))
        fd = os.open(self.trove.logPath, os.W_OK | os.O_CREAT | os.O_APPEND)
        self.logPath = self.trove.logPath
        self.logFd = fd

    def openSocket(self):
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.bind(('', 0))
        self.listen(1)
        self.port = self.socket.getsockname()[1]

    def handle_accept(self):
        csock, caddr = self.accept()
        if self.key:
            key = csock.recv(len(self.key) + 1)
            if key != (self.key + '\n'):
                csock.close()
            csock.send('OK\n')
        # we only need to accept one request.
        self.del_channel()
        self.set_socket(csock)
        self.accepting = False
        self.connected = True

    def close(self):
        asyncore.dispatcher.close(self)
        if self.logFd:
            os.close(self.logFd)
            self.logFd = None
        self._halt = True

    def handle_read(self):
        rv = self.socket.recv(4096)
        if not rv:
            self.connected = False
            self.close()
        else:
            os.write(self.logFd, rv)

    def _signalHandler(self, sigNum, frame):
        server.Server._signalHandler(self, sigNum, frame)
        # we got a signal, but have not finished reading yet.
        if self.connected and self.logFd:
            # keep reading until the socket is closed
            # or until we're killed again.
            self._halt = False

    def writable(self):
        return False
