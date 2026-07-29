from . import common
from struct import pack
from lib import log
import numpy as np


class Encoding:
    framebuffer = None

    name = 'raw'
    id = 0
    description = 'Raw VNC encoding'
    enabled = True
    firstUpdateSent = False

    def __init__(self):
        log.debug("Initialized", __name__)

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
            sendbuff.extend(out.tobytes())
        elif bpp == 16:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            val = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out = np.empty(arr.shape[:2] + (2,), dtype=np.uint8)
            out[:, :, 0] = val & 0xFF
            out[:, :, 1] = (val >> 8) & 0xFF
            sendbuff.extend(out.tobytes())
        else:
            sendbuff.extend(arr.tobytes())

        return sendbuff


common.encodings[common.ENCODINGS.raw] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
