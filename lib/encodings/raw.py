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

class Encoding:
    _buff = None
    framebuffer = None

    name = 'raw'
    id = 0
    description = 'Raw VNC encoding'
    enabled = True
    firstUpdateSent = False

    def __init__(self):
        log.debug("Initialized", __name__)

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        self._buff = bytearray()
        rectangles = 1
        self._buff.extend(pack("!BxH", 0, rectangles))
        self._buff.extend(pack("!HHHH", x, y, w, h))
        self._buff.extend(pack(">i", self.id))

        img_data = image.tobytes()

        if bpp == 16:
            # Client expects BGR565: (B<<11)|(G<<5)|R
            raw = bytearray()
            for i in range(0, len(img_data), 3):
                if i + 2 < len(img_data):
                    r, g, b = img_data[i], img_data[i+1], img_data[i+2]
                    rr = (r >> 3) & 0x1F
                    gg = (g >> 2) & 0x3F
                    bb = (b >> 3) & 0x1F
                    pixel = (bb << 11) | (gg << 5) | rr
                    raw.extend(pack("<H", pixel))
            self._buff.extend(raw)

        elif bpp == 32:
            # Client expects BGRX
            raw = bytearray()
            for i in range(0, len(img_data), 3):
                if i + 2 < len(img_data):
                    r, g, b = img_data[i], img_data[i+1], img_data[i+2]
                    raw.extend(pack("<I", (b << 16) | (g << 8) | r))
            self._buff.extend(raw)

        else:
            self._buff.extend(img_data)

        return self._buff

common.encodings[common.ENCODINGS.raw] = Encoding

log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
