from . import common
from lib import log
from struct import *
from PIL import Image
import zlib


class HextileEncoding:
    name = 'Hextile'
    id = 5
    description = 'Hextile VNC encoding'
    enabled = True
    framebuffer = None

    # Subencoding bit masks (RFC 6143 §7.7.4)
    RAW          = 0x01
    BG_SPECIFIED = 0x02
    FG_SPECIFIED = 0x04
    ANY_SUBRECTS = 0x08
    SUBRECTS_COLORED = 0x10

    TILE_SIZE = 16

    def __init__(self):
        log.debug("Initialized", __name__)
        self._last_bg = None
        self._last_fg = None
        self.bpp = 4  # server uses 32 bpp

    def send_image(self, x, y, w, h, image):
        sendbuff = bytearray()
        rectangles = 1
        sendbuff.extend(pack("!BxH", 0, rectangles))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        if self.framebuffer and self.framebuffer.mode not in ('RGBA', 'RGBX'):
            self.framebuffer = self.framebuffer.convert('RGBX')

        for ty in range(y, y + h, self.TILE_SIZE):
            for tx in range(x, x + w, self.TILE_SIZE):
                tw = min(self.TILE_SIZE, x + w - tx)
                th = min(self.TILE_SIZE, y + h - ty)

                if self.framebuffer:
                    tile = self.framebuffer.crop((tx, ty, tx + tw, ty + th))
                else:
                    tile = image.crop((tx, ty, tx + tw, ty + th))

                sendbuff.extend(self.encode_tile(tile, tw, th))

        return sendbuff

    def encode_tile(self, tile, tw, th):
        encoded = bytearray()
        pixels = list(tile.getdata())
        n_pixels = tw * th

        if len(pixels) == 0:
            return encoded

        # Determine background and check for solid-color tile
        bg = pixels[0]
        all_solid = all(p == bg for p in pixels)

        if all_solid:
            # Solid tile: just specify background (or carry over)
            subenc = self.BG_SPECIFIED
            encoded.append(subenc)
            if not self._bg_carried():
                encoded.extend(self.pack_pixel(bg))
            self._last_bg = bg
            self._last_fg = None
            return encoded

        # Not solid — check if we can use a foreground color for most pixels
        fg_candidates = {}
        for p in pixels:
            fg_candidates[p] = fg_candidates.get(p, 0) + 1
        # Find most common non-background color
        fg_candidates.pop(bg, None)
        if fg_candidates:
            most_common = max(fg_candidates, key=fg_candidates.get)
            most_common_count = fg_candidates[most_common]
        else:
            most_common = bg
            most_common_count = 0

        # Use RRE-style if a non-background color covers >30% of tile
        if most_common_count > n_pixels * 0.3 and most_common != bg:
            subenc = self.BG_SPECIFIED | self.ANY_SUBRECTS
            encoded.append(subenc)
            if not self._bg_carried():
                encoded.extend(self.pack_pixel(bg))
            if most_common != bg and not self._fg_carried(exclude=bg):
                encoded.extend(self.pack_pixel(most_common))
                subenc = self.BG_SPECIFIED | self.FG_SPECIFIED | self.ANY_SUBRECTS
                encoded[0] = subenc
            else:
                pass  # fg carried or same as bg

            self._last_bg = bg
            self._last_fg = most_common if most_common != bg else None

            # Build subrectangles for pixels different from foreground
            non_fg_pixels = [(i, p) for i, p in enumerate(pixels) if p != most_common]
            if not self._fg_carried(exclude=bg) and len(non_fg_pixels) > 0:
                encoded.append(len(non_fg_pixels))

            for idx, color in non_fg_pixels:
                iy = idx // tw
                ix = idx % tw
                # x and y in 4-bit fields
                xy_byte = (ix << 4) | iy
                # width and height are always 1 for single-pixel subrects
                wh_byte = 0  # width-1 = 0, height-1 = 0
                encoded.append(xy_byte)
                encoded.append(wh_byte)
                if subenc & self.SUBRECTS_COLORED or (self._fg_carried(exclude=bg) is False):
                    encoded.extend(self.pack_pixel(color))
        else:
            # Fall back to raw
            subenc = self.RAW
            encoded.append(subenc)
            encoded.extend(tile.tobytes())
            self._last_bg = None
            self._last_fg = None

        return encoded

    def pack_pixel(self, pixel):
        if isinstance(pixel, tuple):
            if len(pixel) >= 3:
                r, g, b = pixel[0], pixel[1], pixel[2]
                if len(pixel) == 4:
                    return pack("!BBBB", r, g, b, pixel[3])
                return pack("!BBBx", r, g, b)
            elif len(pixel) == 1:
                return pack("!BBBB", pixel[0], 0, 0, 0)
        return pack("!BBBB", 0, 0, 0, 0)

    def _bg_carried(self):
        return self._last_bg is not None

    def _fg_carried(self, exclude=None):
        if exclude is not None:
            return self._last_fg is not None and self._last_fg != exclude
        return self._last_fg is not None


common.encodings[common.ENCODINGS.hextile] = HextileEncoding

log.debug("Loaded encoding: %s (%s)" % (__name__, HextileEncoding.id))
