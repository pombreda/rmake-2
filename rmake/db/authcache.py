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


import time

from conary.lib import sha1helper
from conary.dbstore.sqlerrors import DatabaseLocked, ColumnNotUnique


CACHE_TIMEOUT = 15 * 60 # timeout after 15 mins

class AuthenticationCache(object):
    def __init__(self, db):
        self.db = db

    def cache(self, authItemList):
        sessionId =  self._makeSessionId(authItemList)
        timeStamp = time.time() + CACHE_TIMEOUT
        cu = self.db.cursor()
        for x in range(3):
            try:
                cu.execute("DELETE FROM AuthCache WHERE sessionId = ?",
                        cu.binary(sessionId))
                cu.execute("INSERT INTO AuthCache (sessionid, timestamp) "
                        "VALUES (?, ?)", cu.binary(sessionId), timeStamp)
            except (DatabaseLocked, ColumnNotUnique):
                # Race condition -- someone inserted a conflicting value
                # between our statements. Try again.
                self.db.rollback()
            else:
                # Success
                break
        else:
            raise
        self._deleteOld(cu)
        self.db.commit()

    def _deleteOld(self, cu):
        cu.execute('DELETE FROM AuthCache WHERE timeStamp < ?', time.time())

    def resetCache(self):
        cu = self.db.cursor()
        cu.execute('DELETE FROM AuthCache')
        self.db.commit()

    def _makeSessionId(self, authItemList):
        return sha1helper.sha1String('\0'.join([str(x) for x in authItemList]))

    def checkCache(self, authItemList):
        cu = self.db.cursor()
        sessionId = self._makeSessionId(authItemList)
        self._deleteOld(cu)
        match = False
        result = cu.execute('SELECT timeStamp FROM AuthCache WHERE sessionId=?',
                cu.binary(sessionId))
        if result.fetchall():
            match = True
            cu.execute('UPDATE AuthCache SET timeStamp=? WHERE sessionId=?',
                    time.time() + CACHE_TIMEOUT, cu.binary(sessionId))
        self.db.commit()
        return match
