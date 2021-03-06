#!/usr/bin/python
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


"""
Simple client that communicates with rMake.
"""

import errno
import os
import sys


from conary import conarycfg
from conary import errors as conaryerrors
from conary.lib import cfg, log
from conary.lib import options

from rmake import constants
from rmake import errors


sys.excepthook = errors.genExcepthook(debug=False)

from rmake import compat
from rmake import plugins

from rmake.build import buildcfg
from rmake.cmdline import command
from rmake.cmdline import helper


class RmakeMain(options.MainHandler):
    name = 'rmake'
    version = constants.version

    abstractCommand = command.rMakeCommand
    configClass = buildcfg.BuildConfiguration

    useConaryOptions = False

    commandList = command._commands

    def usage(self, rc=1, showAll=False):
        print 'rmake: front end to rMake build tool'
        if not showAll:
            print
            print 'Common Commands (use "rmake help" for the full list)'
        return options.MainHandler.usage(self, rc, showAll=showAll)

    def initializePlugins(self, argv):
        p = plugins.getPluginManager(argv, buildcfg.BuildConfiguration)
        p.callClientHook('client_preInit', self, argv)
        return p

    def getConfigFile(self, argv):
        pluginManager = self.initializePlugins(argv)
        if '--skip-default-config' in argv:
            argv.remove('--skip-default-config')
            read = False
        else:
            read = True

        buildConfig = buildcfg.BuildConfiguration(readConfigFiles=read)
        conaryConfig = conarycfg.ConaryConfiguration(readConfigFiles=read)
        return buildConfig, conaryConfig, pluginManager


    def runCommand(self, thisCommand, (buildConfig, conaryConfig,
                                       pluginManager), argSet, args):
        pluginManager.callClientHook('client_preCommand', self, thisCommand,
                                     (buildConfig, conaryConfig), argSet, args)
        compat.checkRequiredVersions()
        thisCommand.verbose = (log.getVerbosity() <= log.INFO)
        if args[1] != 'help':
            # NOTE: the help system assumes that the base level of output
            # you want is "warning", but rmake is more verbose than that.
            # Due to limitations in how configurable the help system is, 
            # I can't easily fix that.  Someday I should though.  For now,
            # if we're running help, we make log.WARNING the default level,
            # and otherwise log.INFO is the default.
            log.setMinVerbosity(log.INFO)

        # don't let the buildFlavor be overridden yet
        client = helper.rMakeHelper(buildConfig=buildConfig, promptPassword=True)

        pluginManager.callClientHook('client_preCommand2', self, client,
                                     thisCommand)

        try:
            return options.MainHandler.runCommand(self, thisCommand, client, 
                                                  buildConfig, argSet, args)
        except errors.BadParameters:
            if not thisCommand.verbose:
                log.setVerbosity(log.WARNING)
            thisCommand.usage()
            raise

def main(argv):
    log.setVerbosity(log.WARNING)
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
        compat.ConaryVersion().checkRequiredVersion()
        return RmakeMain().main(argv, debuggerException=debuggerException)
    except debuggerException, err:
        raise
    except errors.OpenError, err:
        log.error(str(err) + ' -- check to see that the server is running.')
        return 1
    except (errors.RmakeError, conaryerrors.ConaryError, cfg.ParseError,
            conaryerrors.CvcError), err:
        log.error(err)
        return 1
    except IOError, e:
        # allow broken pipe to exit
        if e.errno != errno.EPIPE:
            raise
    except KeyboardInterrupt:
        return 1
    return 0
