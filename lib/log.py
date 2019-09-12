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

import inspect
import logging


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s: %(message)s')
logger = logging.getLogger('pyvncs')

def _log(*args, logtype='debug'):

    func = inspect.stack()[2][3]
    if func[0] != '<':
        func = "%s():" % func

    _str = func
    
    for s in args:
        _str = "%s %s" % (_str, s)
    _str = _str.strip()
    f = getattr(logger, logtype)
    #logger.debug(str)
    f(_str)

def __getattr__(name):

    def method(*args):
        _str = ''
        if args:
            for s in args:
                _str = "%s %s" % (_str, s)
            _str = _str.strip()

        _log(_str, logtype=name)

    return method




