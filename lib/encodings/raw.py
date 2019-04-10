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

def send_image(x, y, w, h, image):
    _buff = bytearray()
    rectangles = 1
    _buff.extend(pack("!BxH", 0, rectangles))
    _buff.extend(pack("!HHHH", x, y, w, h))
    _buff.extend(pack(">i", common.ENCODINGS.raw))
    _buff.extend( image.tobytes() )

    return _buff

common.ENCODINGS.raw = 0
common.ENCODINGS.raw_send_image = send_image