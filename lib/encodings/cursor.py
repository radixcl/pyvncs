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

import os
import struct
import ctypes
import ctypes.util
import platform
from PIL import Image, ImageChops, ImageDraw, ImagePalette
import base64
from io import BytesIO

from . import common
from struct import *
from lib import log

OS_NAME = platform.system()

if OS_NAME == 'Linux':
    import lib.oshelpers.x11 as x11
    Xcursor = x11.XCursor

if OS_NAME == 'Windows':
    import lib.oshelpers.windows_cursor as windows_cursor


class Encoding:
    name = 'Cursor'
    id = -239
    description = 'Cursor pseudo encoding'
    enabled = True
    pseudoEncoding = True

    cursor_sent = False

    def __init__(self):
        log.debug("Initialized", __name__)
        self.default_cursor_data = 'iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAACc0lEQVR4nK2Wv0sbYRjHP/fDyNUGalKitBDI4FAojg5NF8M7BFIPUkvSopuQoYMgFrl/wbWbndrFuBocAroVHCIdJAaxzdJuNYXGBpIgsU8H7zSpGo3mhS/3Hvfe9/N837t7OI3zobXNhT4Nre2otZ3/7RdEbwOYSqk4MACYbdfuPDTXcKherzeUUklgyAX1BaK5ZkERkUql0lBKpYD7/YJogA94LCJi27YcHh42lVLpfkE0YBAIi4iEw2FJJpNSqVSaSqnX/YB0ACKRiEQiEZmenu4bpAMwNjZ2pnQ6fWfIhcWGYZxpd3eX+fn5wWw2+1Ep9cItxOgFcmGhaZodKhaLLCws3BrSNYGnYrHI4uKiB0n0Ark2gadSqcTS0tLg2traJxficyHaBddeE3gqlUo4juNB4m0proR4Dc4HjIjI92g0ekrWdQzDoNVqsbq6Sjgc7rix0Wg0bdt+tbW1ladLczQvS6DrOo7jsLe3Ry6XY319nUAg8GV2dvY90ASqwFfg51XG/6c4+w5isZjk83nZ39//XS6XZXJyUqampqRarR6HQqEo8Ah4CFj08AzEq8RxHCzL+jExMfGu1Wr9Gh8fp9FosL29PTA3N/cSqLkJmsDJdQnaAScAmqZ9i8fjb2u12ueVlZWcbduYpsnGxgaZTOYNp9vaterLtsgAApubmwXLsp4BIcA/PDz8vFqtHs/MzEgikZCjoyMJBoNPOG0ZPUN8lmWNct5zBoDR5eXl3MHBgezs7EihUCgDI7cBtCfxXl0duKfr+tNUKvUhk8lk/X5/DPDTQy/qVoUH8QEP3PkfoE4PPwU3iemBcI25qTnAP3ZG9LuVtmFhAAAAAElFTkSuQmCC'
        self.default_cursor_img = Image.open(BytesIO(base64.b64decode(self.default_cursor_data)))

    def get_cursor_image(self):
        if OS_NAME == 'Windows':
            return windows_cursor.get_cursor_image()

        elif OS_NAME == 'Linux':
            cursor = Xcursor()
            return cursor.get_cursor_image()

        elif OS_NAME == 'Darwin':
            if self.default_cursor_img.mode != 'RGBA':
                self.default_cursor_img = self.default_cursor_img.convert('RGBA')
            return self.default_cursor_img

        else:
            return None


    def send_cursor(self, x, y, cursor):
        sendbuff = bytearray()
        sendbuff.extend(pack("!B", 0))  # message type 0 == SetCursorPosition
        sendbuff.extend(pack("!H", x))
        sendbuff.extend(pack("!H", y))
        self.cursor_sent = True

        if cursor is not None:
            w, h = cursor.size
            cursor_bytes = cursor.convert("RGBA").tobytes("raw", "BGRA")

            # Invert alpha channel if needed
            pixels = bytearray(cursor_bytes)
            for i in range(0, len(pixels), 4):
                pixels[i + 3] = 255 - pixels[i + 3]

            sendbuff.extend(pack("!B", 1))  # message type 1 == SetCursorShape
            sendbuff.extend(pack("!H", w))  # width
            sendbuff.extend(pack("!H", h))  # height
            sendbuff.extend(pixels)

        else:
            sendbuff.extend(pack("!B", 0))  # message type 0 == SetCursorPosition
            sendbuff.extend(pack("!H", x))
            sendbuff.extend(pack("!H", y))

        return sendbuff


common.encodings[common.ENCODINGS.cursor] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
