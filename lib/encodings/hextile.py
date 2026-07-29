from . import common
from lib import log
from struct import pack
import numpy as np


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
        self._last_bg = None  # wire-format bytes of last background pixel
        self._last_fg = None  # wire-format bytes of last foreground pixel

    def _image_to_wire(self, image, bpp):
        """Convert a PIL RGB image to a (h, w, bpp_bytes) uint8 array in wire format.

        The channel layout matches what the raw encoding produces so that all
        encodings are interchangeable.
        """
        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)
        h, w = arr.shape[:2]

        if bpp == 32:
            out = np.zeros((h, w, 4), dtype=np.uint8)
            out[:, :, :3] = arr[:, :, :3]
            return out
        elif bpp == 16:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            rr = (r >> 3) & 0x1F
            gg = (g >> 2) & 0x3F
            bb = (b >> 3) & 0x1F
            val = (bb << 11) | (gg << 5) | rr
            out = np.zeros((h, w, 2), dtype=np.uint8)
            out[:, :, 0] = val & 0xFF
            out[:, :, 1] = (val >> 8) & 0xFF
            return out
        else:  # 8 bpp
            return arr.reshape(h, w, 1).copy()

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))  # FramebufferUpdate, 1 rect
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        bpp_bytes = (bpp + 7) // 8
        pixels = self._image_to_wire(image, bpp)  # (h, w, bpp_bytes)

        for ty in range(0, h, self.TILE_SIZE):
            for tx in range(0, w, self.TILE_SIZE):
                tw = min(self.TILE_SIZE, w - tx)
                th = min(self.TILE_SIZE, h - ty)
                tile = pixels[ty:ty + th, tx:tx + tw]
                sendbuff.extend(self._encode_tile(tile, bpp_bytes))

        return sendbuff

    def _encode_tile(self, tile, bpp_bytes):
        """Encode a single 16×16 (or smaller) tile.

        Strategy (correct and simple):
          - Solid tile  → BackgroundSpecified (with carry-over optimization)
          - Anything else → Raw
        """
        encoded = bytearray()
        flat = np.ascontiguousarray(tile).reshape(-1, bpp_bytes)

        # View each pixel as a single void record for fast uniqueness check
        rec = np.ascontiguousarray(flat).view(
            np.dtype((np.void, bpp_bytes))
        )
        unique, counts = np.unique(rec, return_counts=True)

        if len(unique) == 1:
            # ---- Solid tile ----
            bg_bytes = flat[0].tobytes()
            subenc = 0
            if bg_bytes != self._last_bg:
                subenc |= self.BG_SPECIFIED
            encoded.append(subenc)
            if subenc & self.BG_SPECIFIED:
                encoded.extend(bg_bytes)
            self._last_bg = bg_bytes
            self._last_fg = None
            return encoded

        # ---- Non-solid: fall back to raw ----
        encoded.append(self.RAW)
        encoded.extend(tile.tobytes())
        # background/foreground carry is unchanged by a raw tile
        return encoded


common.encodings[common.ENCODINGS.hextile] = HextileEncoding

log.debug("Loaded encoding: %s (%s)" % (__name__, HextileEncoding.id))
