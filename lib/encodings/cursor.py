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

from . import common
from struct import *
from lib import log
import zlib

class Encoding:
    name = 'Cursor'
    id = -239
    description = 'Cursor pseudo encoding'
    enabled = True
    pseudoEncoding = True

    cursor_sent = False

    def __init__(self):
        log.debug("Initialized", __name__)

common.encodings[common.ENCODINGS.cursor] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
