#!/usr/bin/python2.4
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

import errno
import os
import sys
import time


from conary import conarycfg
from conary import conaryclient
from conary import errors as conaryerrors
from conary.conaryclient import cmdline
from conary.deps import deps
from conary.lib import cfg, log, util
from conary.lib import options



from rmake import constants
from rmake import errors


sys.excepthook = errors.genExcepthook()

from rmake import compat
from rmake.build import buildcfg
from rmake.build import buildjob
from rmake.cmdline import helper
from rmake.cmdline import query
from rmake.server import servercfg
from rmake.server import server

(NO_PARAM,  ONE_PARAM)  = (options.NO_PARAM, options.ONE_PARAM)
(OPT_PARAM, MULT_PARAM) = (options.OPT_PARAM, options.MULT_PARAM)

class rMakeCommand(options.AbstractCommand):
    docs = {'config'             : ("Set config KEY to VALUE", "'KEY VALUE'"),
            'config-file'        : ("Read PATH config file", "PATH"),
            'context'            : ("Set the conary context to build in"),
            'server-config-file' : ("Read PATH config file", "PATH"),
            'build-config-file'  : ("Read PATH config file", "PATH"),
            'rmake-config-file'  : ("Read PATH config file", "PATH"),
            'skip-default-config': "Don't read default configs",
           }

    def addParameters(self, argDef):
        d = {}
        d["context"] = ONE_PARAM
        d["config"] = MULT_PARAM
        d["server-config"] = MULT_PARAM
        d["server-config-file"] = MULT_PARAM
        d["build-config-file"] = MULT_PARAM
        d["conary-config-file"] = MULT_PARAM
        d["skip-default-config"] = NO_PARAM
        argDef[self.defaultGroup] = d

    def processConfigOptions(self, (buildConfig, serverConfig, conaryConfig),
                             cfgMap, argSet):
        """
            Manage any config maps we've set up, converting 
            assigning them to the config object.
        """ 
        configFileList = argSet.pop('build-config-file', [])
        if not isinstance(configFileList, list):
            configFileList = list(configFileList)

        configFileList.extend(argSet.pop('config-file', []))

        for path in configFileList:
            buildConfig.read(path, exception=True)


        configFileList = argSet.pop('server-config-file', [])
        if not isinstance(configFileList, list):
            configFileList = list(configFileList)
        for path in configFileList:
            serverConfig.read(path, exception=True)

        configFileList = argSet.pop('conary-config-file', [])
        if not isinstance(configFileList, list):
            configFileList = list(configFileList)
        if configFileList and not conaryConfig:
            conaryConfig = conarycfg.ConaryConfiguration(readConfigFiles=False)
        for path in configFileList:
            conaryConfig.read(path, exception=True)

        for (arg, data) in cfgMap.items():
            cfgName, paramType = data[0:2]
            value = argSet.pop(arg, None)
            if value is not None:
                if arg.startswith('no-'):
                    value = not value

                buildConfig.configLine("%s %s" % (cfgName, value))

        for line in argSet.pop('config', []):
            buildConfig.configLine(line)

        for line in argSet.pop('server-config', []):
            serverConfig.configLine(line)

# helper function to get list of commands we support
_commands = []
def register(cmd):
    _commands.append(cmd)

def _getJobIdOrUUId(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        if isinstance(val, str) and len(val) == 32:
            return val
        else:
            raise errors.ParseError, 'Not a valid jobId or UUID: %s' % val

class BuildCommand(rMakeCommand):
    commands = ['build', 'buildgroup']
    paramHelp = '<troveSpec> [<troveSpec>]*'

    docs = {'flavor' : "flavor to build with",
            'host'   : "host to limit build to",
            'poll'   : "show build status as it is updated",
            'quiet'  : "show less build info - don't tail logs",
            'commit' : "commit job when it is done",
            'macro'   : ('set macro NAME to VALUE', "'NAME VALUE'"),
            'no-clean': 'do not remove build directory even if build is'
                        ' successful',
            }

    def addParameters(self, argDef):
        argDef['flavor'] = ONE_PARAM
        argDef['host'] = MULT_PARAM
        argDef['quiet'] = NO_PARAM
        argDef['poll'] = NO_PARAM
        argDef['commit'] = NO_PARAM
        argDef['macro'] = MULT_PARAM
        argDef['no-clean'] = NO_PARAM
        rMakeCommand.addParameters(self, argDef)

    def runCommand(self, client, cfg, argSet, args):
        log.setVerbosity(log.INFO)
        troveSpecs = args[1:]
        flavorSpec = argSet.pop('flavor', None)
        if flavorSpec:
            flavor = deps.parseFlavor(flavorSpec)
            if flavor is None:
                raise errors.ParseError("Invalid flavor: '%s'" % flavorSpec)
            newFlavor = deps.overrideFlavor(client.buildConfig.buildFlavor, 
                                            flavor)
            client.buildConfig.buildFlavor = newFlavor
            newFlavors = []
            for oldFlavor in client.buildConfig.flavor:
                newFlavors.append(deps.overrideFlavor(oldFlavor, flavor))
            client.buildConfig.flavor = newFlavors

        if 'no-clean' in argSet:
            client.buildConfig.cleanAfterCook = False
            del argSet['no-clean']

        macros = argSet.pop('macro', [])
        for macro in macros:
            client.buildConfig.configLine('macros ' + macro)

        hosts = argSet.pop('host', [])
        quiet = argSet.pop('quiet', False)
        commit  = argSet.pop('commit', False)
        if not troveSpecs:
            return self.usage()

        recurseGroups = args[0] == 'buildgroup'
        monitorJob = argSet.pop('poll', False)
        jobId = client.buildTroves(troveSpecs,
                                   limitToHosts=hosts,
                                   recurseGroups=recurseGroups)
        if monitorJob:
            client.poll(jobId, showTroveLogs=not quiet,
                        showBuildLogs=not quiet,
                        commit=commit)
        elif commit:
            client.commitJob(jobId, commitWithFailures=False,
                             waitForJob=True)
        return jobId

register(BuildCommand)


class ChangeSetCommand(rMakeCommand):
    commands = ['changeset']
    paramHelp = '<jobId> path'

    def runCommand(self, client, cfg, argSet, args):
        if len(args) != 3:
            return self.usage()
        jobId = _getJobIdOrUUId(args[1])
        path = args[2]
        client.createChangeSetFile(jobId, path)
register(ChangeSetCommand)

class CommitCommand(rMakeCommand):
    commands = ['commit', 'ci']
    paramHelp = '<jobId>'

    docs = {'commit-outdated-sources' : ("Allow commits of source components when another"
                                         " commit has been made upstream"),
            'source-only'             : "Only commit the source changes" }

    def addParameters(self, argDef):
        argDef['source-only'] = NO_PARAM
        argDef['commit-outdated-sources'] = NO_PARAM
        rMakeCommand.addParameters(self, argDef)

    def runCommand(self, client, cfg, argSet, args):
        commitOutdated = argSet.pop('commit-outdated-sources', False)
        sourceOnly = argSet.pop('source-only', False)
        if len(args) != 2:
            return self.usage()
        jobId = _getJobIdOrUUId(args[1])
        client.commitJob(jobId, commitOutdatedSources=commitOutdated,
                         commitWithFailures=True, waitForJob=True,
                         sourceOnly=sourceOnly)

register(CommitCommand)

class ConfigCommand(rMakeCommand):
    commands = ['config']
    docs = {'show-passwords' : 'do not mask passwords'}

    def addParameters(self, argDef):
         rMakeCommand.addParameters(self, argDef)
         argDef["show-passwords"] = NO_PARAM

    def runCommand(self, client, cfg, argSet, args):
        if len(args) > 1:
            return self.usage()

        showPasswords = argSet.pop('show-passwords', False)
        try:
            prettyPrint = sys.stdout.isatty()
        except AttributeError:
            prettyPrint = False
        client.displayConfig(hidePasswords=not showPasswords,
                             prettyPrint=prettyPrint)
register(ConfigCommand)

class DeleteCommand(rMakeCommand):
    commands = ['delete']
    paramHelp = '<jobId>[-<jobId>]+'

    def runCommand(self, client, cfg, argSet, args):
        toDelete = []
        for arg in args[1:]:
            values = arg.split(',')
            for value in values:
                range = value.split('-', 1)
                if len(range) == 1:
                    toDelete.append(_getJobIdOrUUId(value))
                else:
                    fromVal = _getJobIdOrUUId(range[0])
                    toVal = _getJobIdOrUUId(range[1])
                    if (not isinstance(fromVal, int) 
                        or not isinstance(toVal, int)):
                        raise ParseError('Must use jobIds when specifying'
                                         ' range to delete')
                    toDelete.extend(xrange(fromVal, toVal + 1))
        client.deleteJobs(toDelete)
register(DeleteCommand)


class PollCommand(rMakeCommand):
    commands = ['poll']
    paramHelp = '<jobId>'

    docs = { 'quiet'  : 'Only display major job status changes' }

    def addParameters(self, argDef):
        rMakeCommand.addParameters(self, argDef)
        argDef['quiet'] = NO_PARAM
        argDef['commit'] = NO_PARAM

    def runCommand(self, client, cfg, argSet, args):
        if len(args) != 2:
            self.usage()
            log.error("missing jobId")
            return 1
        log.setVerbosity(log.INFO)
        quiet = argSet.pop('quiet', False)
        commit = argSet.pop('commit', False)
        jobId = _getJobIdOrUUId(args[1])
        client.poll(jobId, showBuildLogs = not quiet,
                    showTroveLogs = not quiet,
                    commit = commit)
register(PollCommand)

class StopCommand(rMakeCommand):
    commands = ['stop']
    paramHelp = '<jobId>'

    def runCommand(self, client, cfg, argSet, args):
        if len(args) != 2:
            self.usage()
            log.error("missing jobId")
            return 1
        log.setVerbosity(log.INFO)
        jobId = _getJobIdOrUUId(args[1])
        client.stopJob(jobId)
register(StopCommand)


class QueryCommand(rMakeCommand):
    commands = ['query', 'q']
    paramHelp = '[<jobId> <troveSpec>*]'

    docs = {'troves'          : 'Display troves for this job',
            'info'            : 'Display details',
            'logs'            : 'Display logs associated with jobs/troves',
            'poll'            : 'Continually update status while job builds',
            'full-versions'   : 'Show full versions',
            'labels'          : 'Show labels',
            'flavors'         : 'Show full flavors',
            'tracebacks'      : 'Show tracebacks'
           }


    def addParameters(self, argDef):
        argDef['troves'] = NO_PARAM
        argDef['info'] = NO_PARAM
        argDef['tracebacks'] = NO_PARAM
        argDef['full-versions'] = NO_PARAM
        argDef['labels']     = NO_PARAM
        argDef['flavors']    = NO_PARAM
        argDef['logs']       = NO_PARAM
        argDef['poll']       = NO_PARAM
        rMakeCommand.addParameters(self, argDef)

    def runCommand(self, client, cfg, argSet, args):
        if len(args) > 1:
            jobId = _getJobIdOrUUId(args[1])
            troveSpecs = args[2:]
            try:
                jobId = int(jobId)
            except ValueError:
                self.usage()
                log.error("bad jobId '%s'", jobId)
                return 1
        else:
            jobId = None
            troveSpecs = []
        displayTroves  = argSet.pop('troves', False)
        displayDetails = argSet.pop('info', False)
        showFullVersions = argSet.pop('full-versions', False)
        showFullFlavors = argSet.pop('flavors', False)
        showLabels = argSet.pop('labels', False)
        showTracebacks = argSet.pop('tracebacks', False)
        showLogs       = argSet.pop('logs', False)
        pollJob       = argSet.pop('poll', False)
        query.displayJobInfo(client, jobId, troveSpecs,
                                    displayTroves=displayTroves,
                                    displayDetails=displayDetails,
                                    showLogs=showLogs,
                                    showBuildLogs=showLogs,
                                    showFullVersions=showFullVersions,
                                    showFullFlavors=showFullFlavors,
                                    showLabels=showLabels,
                                    showTracebacks=showTracebacks)
        if pollJob:
            client.poll(jobId, showBuildLogs = True, showTroveLogs = True)

register(QueryCommand)


class RmakeMain(options.MainHandler):
    name = 'rmake'
    version = constants.version

    abstractCommand = rMakeCommand
    configClass = buildcfg.BuildConfiguration

    commandList = _commands

    @classmethod
    def usage(class_, rc=1):
        print 'rmake: client to communicate with rmake server'
        print ''
        print 'usage:'
        print '  build troveSpec+ - build specified packages'
        print '  changeset jobId  - create a changeset from built packages in a job'
        print '  commit jobId     - commit job to real repository'
        print '  config           - display client configuration information'
        print '  delete jobId[-jobId]+'
        print '                   - delete job(s)'
        print '  poll jobId       - continuously poll for build logs for jobId'
        print '  query/q [jobId] [troveSpec]*'
        print '                   - display information about jobs/troves'
        print '  stop jobId       - stop job'
        return rc

    def getConfigFile(self, argv):
        if '--skip-default-config' in argv:
            argv.remove('--skip-default-config')
            read = False
        else:
            read = True

        serverConfig = servercfg.rMakeConfiguration(readConfigFiles=read)
        buildConfig = buildcfg.BuildConfiguration(readConfigFiles=read)
        conaryConfig = conarycfg.ConaryConfiguration(readConfigFiles=read)
        return buildConfig, serverConfig, conaryConfig

    def _getContext(self, buildConfig, conaryConfig, argSet):
        context = conaryConfig.context
        if buildConfig.context:
            context = buildConfig.context
        if os.path.exists('CONARY'):
            conaryState = compat.ConaryVersion().ConaryStateFromFile('CONARY',
                                                           parseSource=False)
            if conaryState.hasContext():
                context = conaryState.getContext()

        context = os.environ.get('CONARY_CONTEXT', context)
        context = argSet.get('context', context)
        return context

    def runCommand(self, thisCommand, (buildConfig, serverConfig, conaryConfig),
                   argSet, args):
 
        context = self._getContext(buildConfig, conaryConfig, argSet)
        if conaryConfig and context:
            conaryConfig.setContext(context)

        buildConfig.useConaryConfig(conaryConfig)
        if context:
            buildConfig.setContext(context)

        buildConfig.initializeFlavors()

        client = helper.rMakeHelper(buildConfig=buildConfig,
                                    rmakeConfig=serverConfig)
        options.MainHandler.runCommand(self, thisCommand, client, buildConfig, 
                                       argSet, args[1:])

def main(argv):
    log.setVerbosity(log.INFO)
    rmakeMain = RmakeMain()
    try:
        argv = list(argv)
        debugAll = '--debug-all' in argv
        if debugAll:
            debuggerException = Exception
            argv.remove('--debug-all')
        else:
            debuggerException = errors.RmakeInternalError
        sys.excepthook = errors.genExcepthook(debug=debugAll,
                                              debugCtrlC=debugAll)
        return RmakeMain().main(argv, debuggerException=debuggerException)
    except debuggerException, err:
        raise
    except (errors.RmakeError, conaryerrors.ConaryError, cfg.ParseError,
            conaryerrors.CvcError), err:
        log.error(err)
        sys.exit(1)
    except IOError, e:
        # allow broken pipe to exit
        if e.errno != errno.EPIPE:
            raise
    except KeyboardInterrupt:
        pass
