#
# Copyright (c) 2006 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.opensource.org/licenses/cpl.php.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#
"""
Simple client that communicates with rMake.
"""

import itertools
from optparse import OptionParser
import os
import sys
import tempfile
import time

from conary import conarycfg
from conary import conaryclient
from conary import state
from conary.build import use
from conary.deps import deps
from conary.lib import log
from conary.lib import options

from rmake.build import buildcfg
from rmake.build import buildjob
from rmake.cmdline import buildcmd
from rmake.cmdline import commit
from rmake.cmdline import monitor
from rmake.server import servercfg
from rmake.server import server
from rmake import compat
from rmake import plugins

class rMakeHelper(object):

    def __init__(self, uri=None, rmakeConfig=None, buildConfig=None, root='/',
                 guiPassword=False):
        if not rmakeConfig:
            rmakeConfig = servercfg.rMakeConfiguration()

        if not buildConfig:
            buildConfig = buildcfg.BuildConfiguration(True, root)
            if conaryConfig:
                buildConfig.useConaryConfig(conaryConfig)

        if uri is None:
            uri = rmakeConfig.getServerUri()

        self.client = server.rMakeClient(uri)

        # this should use extend but extend used to be broken when
        # there were multiple entries
        for info in reversed(rmakeConfig.user):
            buildConfig.user.append(info)
        buildConfig.initializeFlavors()
        use.setBuildFlagsFromFlavor(None, buildConfig.buildFlavor, error=False)

        if guiPassword:
            try:
                from rmake.cmdline import password
                pwPrompt = password.PasswordPrompter(buildConfig).getPassword
            except ImportError, err:
                log.error('Could not load gtk password prompter - %s', err)
                pwPrompt = None
        else:
            pwPrompt = None

        self.conaryclient = conaryclient.ConaryClient(buildConfig,
                                                      passwordPrompter=pwPrompt)
        self.repos = self.conaryclient.getRepos()
        self.buildConfig = buildConfig
        self.rmakeConfig = rmakeConfig
        self.buildConfig.setServerConfig(rmakeConfig)



    def displayConfig(self, hidePasswords=True, prettyPrint=True):
        self.buildConfig.setDisplayOptions(hidePasswords=hidePasswords,
                                           prettyPrint=prettyPrint)
        self.buildConfig.display()

    def buildTroves(self, troveSpecList,
                    limitToHosts=None, recurseGroups=False):
        toBuild = buildcmd.getTrovesToBuild(self.conaryclient,
                                            troveSpecList,
                                            limitToHosts=limitToHosts)

        jobId = self.client.buildTroves(toBuild, self.buildConfig)
        print 'Added Job %s' % jobId
        for (n,v,f) in sorted(toBuild):
            if f is not None and not f.isEmpty():
                f = '[%s]' % f
            else:
                f = ''
            print '  %s=%s/%s%s' % (n, v.trailingLabel(), v.trailingRevision(), f)

        return jobId

    def stopJob(self, jobId):
        stopped = self.client.stopJob(jobId)


    def createChangeSet(self, jobId):
        job = self.client.getJob(jobId)
        binTroves = []
        for troveTup in job.iterTroveList():
            trove = job.getTrove(*troveTup)
            binTroves.extend(trove.iterBuiltTroves())
        if not binTroves:
            log.error('No built troves associated with this job')
        jobList = [(x[0], (None, None), (x[1], x[2]), True) for x in binTroves]
        primaryTroveList = [ x for x in binTroves if ':' not in x[0]]
        cs = self.repos.createChangeSet(jobList, recurse=False,
                                        primaryTroveList=primaryTroveList)
        return cs

    def createChangeSetFile(self, jobId, path):
        job = self.client.getJob(jobId)
        binTroves = []
        for troveTup in job.iterTroveList():
            trove = job.getTrove(*troveTup)
            binTroves.extend(trove.iterBuiltTroves())
        if not binTroves:
            log.error('No built troves associated with this job')
            return
        jobList = [(x[0], (None, None), (x[1], x[2]), True) for x in binTroves]
        primaryTroveList = [ x for x in binTroves if ':' not in x[0]]
        self.repos.createChangeSetFile(jobList, path, recurse=False, 
                                       primaryTroveList=primaryTroveList)

    def commitJob(self, jobId, message=None, commitOutdatedSources=False,
                  commitWithFailures=True, waitForJob=False):
        job = self.client.getJob(jobId)
        if not job.isFinished() and waitForJob:
            print "Waiting for job %s to complete before committing" % jobId
            try:
                self.waitForJob(jobId)
            except Exception, err:
                print "Wait interrupted, not committing"
                print "You can restart commit by running 'rmake commit %s'" % jobId
                raise
        if not job.isFinished():
            log.error('This job is not yet finished')
            return False
        if job.isFailed() and not commitWithFailures:
            log.error('This job has failures, not committing')
            return False
        if not list(job.iterBuiltTroves()):
            log.error('This job has no built troves to commit')
            return False
        self.client.startCommit(jobId)
        try:
            succeeded, data = commit.commitJob(self.conaryclient, job,
                                   self.rmakeConfig, message,
                                   commitOutdatedSources=commitOutdatedSources)
            if succeeded:
                self.client.commitSucceeded(jobId, data)
                print 'Committed:\n',
                print ''.join('    %s=%s[%s]\n' % x for x in sorted(data)),
                return True
            else:
                self.client.commitFailed(jobId, data)
                log.error(data)
                return False
        except Exception, err:
            self.client.commitFailed(jobId, str(err))
            log.error(err)
            return False

    def deleteJobs(self, jobIdList):
        deleted = self.client.deleteJobs(jobIdList)
        print 'deleted %d jobs' % len(deleted)

    def waitForJob(self, jobId):
        fd, tmpPath = tempfile.mkstemp()
        os.close(fd)
        uri = 'unix:' + tmpPath
        try:
            jobMonitor = monitor.waitForJob(self.client, jobId, uri)
        finally:
            os.remove(tmpPath)

    def poll(self, jobId, showTroveLogs = False, showBuildLogs = False,
             commit = False):
        fd, tmpPath = tempfile.mkstemp()
        os.close(fd)
        uri = 'unix:' + tmpPath
        try:
            try:
                jobMonitor = monitor.monitorJob(self.client, jobId, uri,
                                            showTroveLogs = showTroveLogs,
                                            showBuildLogs = showBuildLogs)
            except Exception, err:
                if commit:
                    print "Poll interrupted, not committing"
                    print "You can restart commit by running 'rmake poll %s --commit'" % jobId
                raise
            if commit:
                self.commitJob(jobId, commitWithFailures=False)
        finally:
            os.remove(tmpPath)
