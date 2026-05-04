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
from lib import log
from struct import *

TILE_W = 16
TILE_H = 16
RAW_THRESHOLD = 50
MAX_SUBRECTS = 72


class Encoding:
    name = 'hextile'
    id = 5
    description = 'Hextile VNC encoding'
    enabled = True
    firstUpdateSent = False

    def __init__(self):
        log.debug("Initialized", __name__)

    def _rgb16_to_bgr565(self, r, g, b):
        rr = (r >> 3) & 0x1F
        gg = (g >> 2) & 0x3F
        bb = (b >> 3) & 0x1F
        return (rr << 11) | (gg << 5) | bb

    def _rgb32_to_bgrx(self, r, g, b):
        return (b << 16) | (g << 8) | r

    def _read_pixel(self, data, offset, bpp_bytes):
        c = 0
        for j in range(bpp_bytes):
            c = (c << 8) | data[offset + j]
        return c

    def _count_different(self, tile_bytes, bg_color, bpp_bytes):
        count = 0
        for i in range(0, len(tile_bytes), bpp_bytes):
            c = self._read_pixel(tile_bytes, i, bpp_bytes)
            if c != bg_color:
                count += 1
        return count

    def _find_subrects(self, tile_bytes, bg_color, bpp_bytes):
        different = []
        for py in range(16):
            for px in range(16):
                offset = (py * 16 + px) * bpp_bytes
                if offset + bpp_bytes > len(tile_bytes):
                    break
                c = self._read_pixel(tile_bytes, offset, bpp_bytes)
                if c != bg_color:
                    different.append((px, py))

        if not different:
            return []

        different.sort(key=lambda p: (p[1], p[0]))

        subrects = []
        used = set()

        for px, py in different:
            if (px, py) in used:
                continue

            off = (py * 16 + px) * bpp_bytes
            color = self._read_pixel(tile_bytes, off, bpp_bytes)

            max_w = 0
            max_h = 0
            for w in range(1, 17 - px):
                h = 0
                while h < 16 - py:
                    check_x = px + w - 1
                    check_y = py + h
                    if (check_x, check_y) in used:
                        break
                    check_off = (check_y * 16 + check_x) * bpp_bytes
                    if check_off + bpp_bytes > len(tile_bytes):
                        break
                    c2 = self._read_pixel(tile_bytes, check_off, bpp_bytes)
                    if c2 != color:
                        break
                    h += 1
                if h > max_h:
                    max_w = w
                    max_h = h

            if max_h == 0:
                max_h = 1
                max_w = 1

            subrects.append((px, py, max_w * max_h))
            for sy in range(max_h):
                for sx in range(max_w):
                    used.add((px + sx, py + sy))

            if len(subrects) >= MAX_SUBRECTS:
                break

        return subrects

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()
        rectangles = 1
        sendbuff.extend(pack("!BxH", 0, rectangles))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        img_data = image.tobytes()
        bpp_bytes = bpp // 8

        # Convert RGB to client pixel format
        if bpp == 16:
            tile_data = bytearray()
            for i in range(0, len(img_data), 3):
                if i + 2 < len(img_data):
                    r, g, b = img_data[i], img_data[i+1], img_data[i+2]
                    pixel = self._rgb16_to_bgr565(r, g, b)
                    tile_data.extend(pack("<H", pixel))
            bpp_bytes = 2
        elif bpp == 32:
            tile_data = bytearray()
            for i in range(0, len(img_data), 3):
                if i + 2 < len(img_data):
                    r, g, b = img_data[i], img_data[i+1], img_data[i+2]
                    tile_data.extend(pack("<I", self._rgb32_to_bgrx(r, g, b)))
            bpp_bytes = 4
        else:
            tile_data = bytearray(img_data)
            bpp_bytes = bpp // 8

        num_tiles_x = (w + TILE_W - 1) // TILE_W
        num_tiles_y = (h + TILE_H - 1) // TILE_H

        for ty in range(num_tiles_y):
            for tx in range(num_tiles_x):
                tw = min(TILE_W, w - tx * TILE_W)
                th = min(TILE_H, h - ty * TILE_H)

                tile_bytes = bytearray()
                for row in range(th):
                    for col in range(tw):
                        src_off = ((ty * TILE_H + row) * w + (tx * TILE_W + col)) * bpp_bytes
                        end_off = src_off + bpp_bytes
                        if end_off > len(tile_data):
                            break
                        tile_bytes.extend(tile_data[src_off:end_off])

                if len(tile_bytes) < bpp_bytes:
                    continue

                bg_color = self._read_pixel(tile_bytes, 0, bpp_bytes)

                all_same = True
                for i in range(bpp_bytes, len(tile_bytes), bpp_bytes):
                    c = self._read_pixel(tile_bytes, i, bpp_bytes)
                    if c != bg_color:
                        all_same = False
                        break

                if all_same:
                    sendbuff.append(4)
                    sendbuff.extend(pack(">I", bg_color))
                    continue

                diff_count = self._count_different(tile_bytes, bg_color, bpp_bytes)

                if diff_count == 0:
                    sendbuff.append(4)
                    sendbuff.extend(pack(">I", bg_color))
                elif diff_count > RAW_THRESHOLD:
                    sendbuff.append(0)
                    sendbuff.extend(tile_bytes)
                elif diff_count <= 2:
                    sendbuff.append(2)
                    sendbuff.extend(pack(">I", bg_color))
                    sendbuff.append(0)
                    for i in range(0, len(tile_bytes), bpp_bytes):
                        c = self._read_pixel(tile_bytes, i, bpp_bytes)
                        if c != bg_color:
                            sendbuff.extend(pack(">I", c))
                else:
                    sendbuff.append(3)
                    sendbuff.extend(pack(">I", bg_color))
                    subrects = self._find_subrects(tile_bytes, bg_color, bpp_bytes)
                    sendbuff.append(len(subrects))
                    for sx, sy, sc in subrects:
                        sendbuff.extend(pack(">I", (ty * TILE_H + sy) * w + (tx * TILE_W + sx)))

        return bytes(sendbuff)

common.encodings[common.ENCODINGS.hextile] = Encoding

log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
