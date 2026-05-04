from . import common
from lib import log
from struct import *
import zlib


class Encoding:
    name = 'zlib'
    id = 6
    description = 'zlib VNC encoding'
    enabled = True
    firstUpdateSent = False
    _compressObj = None

    def __init__(self):
        log.debug("Initialized", __name__)
        self._compressObj = zlib.compressobj(
                zlib.Z_DEFAULT_COMPRESSION,
                zlib.DEFLATED,
                zlib.MAX_WBITS,
                zlib.DEF_MEM_LEVEL,
                zlib.Z_DEFAULT_STRATEGY
        )

    def _rgb16_to_bgr565(self, r, g, b):
        rr = (r >> 3) & 0x1F
        gg = (g >> 2) & 0x3F
        bb = (b >> 3) & 0x1F
        return (rr << 11) | (gg << 5) | bb

    def _rgb32_to_bgrx(self, r, g, b):
        return (b << 16) | (g << 8) | r

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()

        rectangles = 1
        sendbuff.extend(pack("!BxH", 0, rectangles))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        img_data = image.tobytes()

        if bpp == 16:
            raw = bytearray()
            for i in range(0, len(img_data), 3):
                if i + 2 < len(img_data):
                    r, g, b = img_data[i], img_data[i+1], img_data[i+2]
                    pixel = self._rgb16_to_bgr565(r, g, b)
                    raw.extend(pack("<H", pixel))
            zlibdata = self._compressObj.compress(bytes(raw))
            zlibdata += self._compressObj.flush(zlib.Z_FULL_FLUSH)
        elif bpp == 32:
            raw = bytearray()
            for i in range(0, len(img_data), 3):
                if i + 2 < len(img_data):
                    r, g, b = img_data[i], img_data[i+1], img_data[i+2]
                    raw.extend(pack("<I", self._rgb32_to_bgrx(r, g, b)))
            zlibdata = self._compressObj.compress(bytes(raw))
            zlibdata += self._compressObj.flush(zlib.Z_FULL_FLUSH)
        else:
            zlibdata = self._compressObj.compress(img_data)
            zlibdata += self._compressObj.flush(zlib.Z_FULL_FLUSH)

        l = pack("!I", len(zlibdata))
        sendbuff.extend(l)
        sendbuff.extend(zlibdata)

        return sendbuff

common.encodings[common.ENCODINGS.zlib] = Encoding

log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
