from . import common
from lib import log
from struct import pack
import zlib
import numpy as np


class Encoding:
    name = 'zlib'
    id = 6
    description = 'zlib VNC encoding'
    enabled = True
    firstUpdateSent = False
    framebuffer = None

    def __init__(self):
        log.debug("Initialized", __name__)
        self._compression_level = 1
        self._compressObj = self._new_stream()

    def _new_stream(self):
        return zlib.compressobj(
            self._compression_level, zlib.DEFLATED,
            zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY)

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)

        if bpp == 32:
            # rfb_bitmap has already swapped R↔B when primaryOrder == "bgr"
            # (the case for little-endian rgb888, the most common format), so
            # arr is in wire order: byte 0 = B, byte 2 = R.  We must NOT
            # re-swap here, otherwise red and blue are exchanged on screen.
            out = np.zeros(arr.shape[:2] + (4,), dtype=np.uint8)
            out[:, :, :3] = arr[:, :, :3]
            raw = out.tobytes()
        elif bpp == 16:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            val = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out = np.empty(arr.shape[:2] + (2,), dtype=np.uint8)
            out[:, :, 0] = val & 0xFF
            out[:, :, 1] = (val >> 8) & 0xFF
            raw = out.tobytes()
        else:
            raw = arr.tobytes()

        zlibdata = self._compressObj.compress(raw)
        zlibdata += self._compressObj.flush(zlib.Z_FULL_FLUSH)

        sendbuff.extend(pack("!I", len(zlibdata)))
        sendbuff.extend(zlibdata)
        return sendbuff


common.encodings[common.ENCODINGS.zlib] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
