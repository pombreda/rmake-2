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
Generic plugin library.  Loads plugins from the given paths, unless they
are disabled.

Plugins can import each other by using the given pluginPrefix - the importer
hook will find other plugins in whatever directory they are installed.

TODO: add support for a requires syntax for plugins, and versioning of
plugin APIs.
"""
import imp
import os
import sys
import traceback

from conary.lib import log

class Plugin(object):
    """
        Basic plugin.  Should be derived from by plugins.
    """
    types = []
    def __init__(self, name, path, pluginManager):
        self.name = name
        self.path = path
        self.pluginManager = pluginManager
        self.enabled = True

    def unload(self):
        pass

class PluginManager(object):
    """
        The plugin manager loads available plugins and then
        stores them for later so that they are available for calling
        hooks in them.

        Unless the store and remove methods are overridden,
        plugins have types which can be used to call to hooks on only
        particular classes.

        Parameters for initialization:
        @param pluginDirs: directories to search for plugins

        @param disabledPlugins: a list of plugin names to not be loaded.

        @param pluginPrefix: when plugins access themselves and other
            plugins internally, they do so through import statements 
            underneath a particular prefix.  This defines the prefix.

        @param pluginClass: the class from which plugins should derive.
            This class is checked before instantiating plugins in a loaded
            plugin module.

        @param supportedTypes: the different types which plugins 
            are allowed to be.  Defaults to empty, effectively disabling
            types.
    """

    def __init__(self, pluginDirs=None, disabledPlugins=None,
                 pluginPrefix='__plugins__', pluginClass=Plugin):
        if pluginDirs is None:
            pluginDirs = []
        if disabledPlugins is None:
            disabledPlugins = []

        self.pluginDirs = pluginDirs
        self.pluginPrefix = pluginPrefix
        self.pluginClass = pluginClass

        self.plugins = []
        self.pluginsByType = {}
        self.pluginsByName = {}
        self.disabledPlugins = disabledPlugins

        self.loader = PluginImporter(self.pluginDirs, self.pluginPrefix,
                                     logger=self)

    def installImporter(self):
        self.loader.install()

    def uninstallImporter(self):
        self.loader.uninstall()

    def disableAllPlugins(self):
        for plugin in self.plugins:
            plugin.enabled = False

    def hasPlugin(self, name):
        return name in self.pluginsByName

    def enablePlugin(self, name):
        self.pluginsByName[name].enabled = True

    def disablePlugin(self, name):
        self.pluginsByName[name].enabled = False

    def getPluginsByType(self, pluginType):
        return sorted(self.pluginsByType.get(pluginType, []), 
                      key=lambda x: x.name)

    def getPlugin(self, name):
        return self.pluginsByName[name]

    def warning(self, txt, *args, **kw):
        log.warning(txt, *args, **kw)

    def loadFailed(self, path, txt):
        self.warning('Failed to import plugin %s: %s' % (path, txt))

    def isValidPluginFileName(self, dir, fileName):
        if fileName.startswith('.') or fileName.startswith('~'):
            return False
        path = dir + '/' + fileName
        if os.path.isdir(path):
            if (os.path.exists(path + '/__init__.py') 
                or os.path.exists(path + '/__init__.pyc')):
                return True
        elif path.endswith('.py') or path.endswith('.pyc'):
            return True
        return False

    def loadPlugins(self):
        self.loader.install()
        for dir in self.pluginDirs:
            if not os.path.isdir(dir):
                continue
            pluginFiles = {}
            for fileName in os.listdir(dir):
                if not self.isValidPluginFileName(dir, fileName):
                    continue
                if fileName.endswith('.pyc'):
                    pluginFileName = fileName[:-1]
                else:
                    pluginFileName = fileName
                if pluginFileName in pluginFiles:
                    otherFileName = pluginFiles[pluginFileName]
                    otherFileMtime = os.stat(dir + '/' + otherFileName).st_mtime
                    thisFileMtime = os.stat(dir + '/' + fileName).st_mtime
                    if otherFileMtime > thisFileMtime:
                        continue
                pluginFiles[pluginFileName] = fileName
            pluginFiles = pluginFiles.values()
            for fileName in pluginFiles:
                plugin = self.loadPluginFromFileName(dir, fileName)
                if plugin is not None:
                    self.storePlugin(plugin)
        self.loader.uninstall()

    def unloadPlugin(self, name):
        plugin = self.pluginsByName.pop(name)
        plugin.unload()
        for type in plugin.types + ['all']:
            self.pluginsByType[type].remove(plugin)

    def storePlugin(self, plugin):
        if plugin.name in self.pluginsByName:
            oldPlugin = self.pluginsByName[plugin.name]
            self.plugins.remove(oldPlugin)
            for type in oldPlugin.types + ['all']:
                self.pluginsByType.setdefault(type, []).remove(oldPlugin)
        self.pluginsByName[plugin.name] = plugin
        self.plugins.append(plugin)
        for type in plugin.types + ['all']:
            self.pluginsByType.setdefault(type, []).append(plugin)

    def getPluginNameFromFile(self, fileName):
        # module names cannot have .'s in them - cut off the extension 
        # whatever it is.
        return fileName.split('.', 1)[0]

    def validatePlugin(self, plugin):
        return plugin

    def loadPluginFromFileName(self, dir, fileName):
        name = self.getPluginNameFromFile(fileName)
        if name in self.disabledPlugins:
            return None

        # use our importer hook to load the plugin.
        # The fourth parameter in the __import__ statement tells python
        # to return the actual child module instead of the parent module.
        try:
            pluginModule = __import__('%s.%s' % (self.pluginPrefix, name),
                                      {}, {}, [self.pluginPrefix])
        except ImportError:
            return None
        plugin = self.getPluginFromModule(pluginModule, name)
        if plugin is not None:
            plugin = self.validatePlugin(plugin)
        return plugin

    def getPluginFromModule(self, pluginModule, name):
        """
            Given the module in the plugin directory, return the plugin(s)
            contained in that module.

            In this case, we instantiate all such plugins
        """
        plugins = []
        for class_ in pluginModule.__dict__.itervalues():
            if (isinstance(class_, type)
                and issubclass(class_, self.pluginClass)):
                plugin = class_(name, pluginModule.__file__, self)
                plugins.append(plugin)
        if len(plugins) > 1:
            self.loadFailed(pluginModule.__file__,
                        "Can only define one plugin in a plugin module")
            return None
        if not plugins:
            return None
        return plugins[0]

    def callHook(self, type, hookName, *args, **kw):
        self.loader.install()
        for plugin in self.getPluginsByType(type):
            if not getattr(plugin, 'enabled', True):
                continue
            getattr(plugin, hookName)(*args, **kw)
        self.loader.uninstall()


class PluginImporter(object):
    """
        Plugin importer is a meta_path import hook
        ala PEP 302 (http://www.python.org/dev/peps/pep-0302/).

        When installed, it looks for imports that start with
        pluginPrefix, (e.g. rmake_plugins), and searches for modules
        in pluginDirs that match the rest of the import.

        For example, if I set up my pluginDirs as ['/tmp/foo', '/tmp/bar'],
        and my plugin dir as foo_plugins, then I could import a python
        file at /tmp/foo/bar.py as "from foo_plugins import bar".
        Similarly, I could use "from foo_plugins import bam.baz" to
        import /tmp/bar/bam/baz.py

        The only methods that should be called by regular users
        are install(), uninstall(), and find_plugin(name).

        install: activates this import hook
        uninstall: deactivates this import hook
        find_plugin: used to determine whether a plugin exists
          without actually importing it.
    """

    def __init__(self, pluginDirs, pluginPrefix, logger=None):
        self.pluginPrefix = pluginPrefix
        self.pluginDirs = pluginDirs
        if logger is None:
            logger = self
        self.logger = logger

    def warning(self, txt, *args, **kw):
        log.warning(txt, *args, **kw)

    def loadFailed(self, path, txt):
        self.warning('Failed to import plugin %s: %s' % (path, txt))

    def find_module(self, fullname, path=None):
        if fullname == self.pluginPrefix:
            if path:
                # rbuild_plugins is not relative to anything else,
                # so any path means we're not finding the right thing
                return None
            return self

        if '.' not in fullname:
            return None

        head, fullname = fullname.split('.', 1)
        if head != self.pluginPrefix:
            return None
        if not self.find_plugin(fullname):
            return None
        return self

    def load_module(self, modname):
        m = imp.new_module(modname)
        m.__path__ = [self.pluginPrefix]
        sys.modules[modname] = m
        m.__name__ = modname
        m.__loader__ = self
        m.pluginMgr = self
        if modname == self.pluginPrefix:
            m.__file__ = self.pluginDirs[0]
            return m
        assert(modname.startswith(self.pluginPrefix + '.'))
        modname = modname.split('.', 1)[1]
        path = self.find_plugin(modname)
        m.__file__ = path
        try:
            execfile(path, m.__dict__)
            return m
        except Exception, err:
            self.logger.loadFailed(path, '%s\n%s' % (err, traceback.format_exc()))
            if isinstance(err, ImportError):
                raise
            else:
                raise ImportError(str(err))

    def find_plugin(self, name):
        """
            Given a plugin name (possibly with dots in it), find 
            the path to that plugin if possible.
        """
        for dir in self.pluginDirs:
            # FIXME: can I use imp.find_module here?
            base = dir
            rest = name
            while '.' in rest:
                head, rest = rest.split('.', 1)
                path = base + '/' + head
                if os.path.isdir(path):
                    base = path
                else:
                    break
            path = base + '/' + rest
            if os.path.isdir(path): 
                return '%s/__init__.py' % (path)
            if os.path.exists(path + '.py'):
                return path + '.py'
            if os.path.exists(path + '.pyc'):
                return path + '.pyc'

    def install(self):
        # install on meta_path so that this will get searched before sys.path...
        sys.meta_path.insert(0, self)

    def uninstall(self):
        sys.meta_path.remove(self)
