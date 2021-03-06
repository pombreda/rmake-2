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


import SimpleXMLRPCServer
import signal
import os
import time
import xmlrpclib

from rmake.lib import logfile

class MockRbuilder(object):
    def __init__(self, port, logPath):
        self.port = port
        self.url = 'http://localhost:%s' %  port
        self.user = ('foo', 'bar')
        self.pid = None
        self.logPath = logPath

    def start(self):
        pid = os.fork()
        if pid:
            self.server = xmlrpclib.ServerProxy(self.url)
            self.pid = pid
            def checkPid():
                if os.waitpid(pid, os.WNOHANG)[0]:
                    raise RuntimeError('Mock Rbuilder Died!')
            self.ping(checkFn=checkPid)
        else:
            try:
                from rmake.lib import logfile
                self.logFile = logfile.LogFile(self.logPath)
                self.logFile.redirectOutput()
                server = SimpleXMLRPCServer.SimpleXMLRPCServer(
                        ("localhost", self.port), MockRbuilderHandler)
                server.register_instance(MockRbuilderServer())
                server.serve_forever()
            finally:
                os._exit(0)

    def ping(self, tries=100, sleep=0.1, checkFn=None):
        for i in xrange(tries):
            try:
                return self.server.ping()
            except Exception:
                if checkFn:
                    checkFn()
                time.sleep(sleep)
        raise
                

    def stop(self):
        os.kill(signal.SIGKILL, self.pid)
        os.waitpid(self.pid, 0)


class MockRbuilderHandler(SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
    rpc_paths = ('/', '/RPC2', '/xmlrpc-private')

            
class MockRbuilderServer(object):
    def __init__(self):
        self.imageBuilds = {}

    def ping(self):
        return (False, True)

    def _dispatch(self, method, params):
        try:
            return False, getattr(self, method)(*params)
        except Exception, e:
            return True, [str(e)]

    def getProjectIdByHostname(self, projectName):
        return 31

    def startImageJob(self, buildId):
        curStatus, options = self.imageBuilds[buildId]
        self.imageBuilds[buildId] = curStatus + 1, options
        return ''

    def newBuildWithOptions(self, productId, projectName, troveName,
                            troveVersion, troveFlavor,
                            imageType, imageOptions):
        buildId = len(self.imageBuilds)
        self.imageBuilds[buildId] = (0, imageOptions)
        return buildId


    def getBuildStatus(self, buildId):
        curStatus, options = self.imageBuilds[buildId]
        newStatus = curStatus + 50
        if newStatus > 200:
            self.imageBuilds.pop(buildId)
            return {'status' : newStatus, 'message' : 'Finished.'}
        self.imageBuilds[buildId] = (newStatus, options)
        return {'message' : 'Working: %s' % newStatus, 'status' : newStatus}
