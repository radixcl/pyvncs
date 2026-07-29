from . import common
from lib import log
import zlib
import numpy as np
from struct import pack


class Encoding:
    """ZRLE encoding (RFC 6143 §7.6.9).

    Combines zlib compression with per-tile subencodings:
      0       Raw CPIXEL data
      1       Solid tile (single colour)
      2-16    Packed palette (2-16 colours, bits per index 1/2/4)
      128     Plain RLE
      129-255 Palette RLE (palette size = subencoding - 128)

    A single persistent zlib stream is used for the whole connection.
    """

    name = 'zrle'
    id = 16
    description = 'ZRLE VNC encoding'
    enabled = True
    framebuffer = None
    firstUpdateSent = False

    TILE = 64

    def __init__(self):
        log.debug("Initialized", __name__)
        self._comp = zlib.compressobj(
            zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED,
            zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        cps = self._cpixel_size(bpp, depth)
        tile_data = bytearray()

        for ty in range(0, h, self.TILE):
            for tx in range(0, w, self.TILE):
                tw = min(self.TILE, w - tx)
                th = min(self.TILE, h - ty)
                tile = image.crop((tx, ty, tx + tw, ty + th))
                tile_data.extend(self._encode_tile(tile, cps, bpp, depth))

        compressed = self._comp.compress(bytes(tile_data))
        compressed += self._comp.flush(zlib.Z_SYNC_FLUSH)

        sendbuff.extend(pack("!I", len(compressed)))
        sendbuff.extend(compressed)
        return sendbuff

    # ------------------------------------------------------------------
    # CPIXEL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cpixel_size(bpp, depth):
        if bpp == 32 and depth == 24:
            return 3
        return max(bpp // 8, 1)

    def _pixels_as_bytes(self, image, bpp, depth):
        """Return (flat_bytes, cpixel_size, width, height).

        CPIXEL is always plain RGB per the ZRLE spec (first byte = red,
        second = green, third = blue), regardless of the pixel format's
        shift values.  rfb_bitmap swaps R↔B for "bgr" primaryOrder (needed
        by raw/zlib), so we undo that swap here.
        """
        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)
        if getattr(self, 'primaryOrder', 'rgb') == 'bgr' and arr.shape[2] >= 3:
            arr = arr[:, :, [2, 1, 0]]
        h, w = arr.shape[:2]
        cps = Encoding._cpixel_size(bpp, depth)

        if cps == 3:
            return arr[:, :, :3].tobytes(), 3, w, h
        if cps == 2:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            val = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out = np.empty((h, w, 2), dtype=np.uint8)
            out[:, :, 0] = val & 0xFF
            out[:, :, 1] = (val >> 8) & 0xFF
            return out.tobytes(), 2, w, h
        return arr.tobytes(), 1, w, h

    # ------------------------------------------------------------------
    # tile encoding
    # ------------------------------------------------------------------

    def _encode_tile(self, tile, cps, bpp, depth):
        data, cps, tw, th = self._pixels_as_bytes(tile, bpp, depth)
        n = tw * th

        # Build list of cpixel byte-strings for hashing / comparison
        px = [data[i * cps:(i + 1) * cps] for i in range(n)]

        # Count unique colours
        freq = {}
        for p in px:
            freq[p] = freq.get(p, 0) + 1
        ncol = len(freq)

        # --- solid -------------------------------------------------------
        if ncol == 1:
            return bytes([1]) + next(iter(freq))

        # --- collect candidates, pick smallest ---------------------------
        best = None

        # raw (subencoding 0)
        raw = bytes([0]) + data
        best = raw

        # packed palette (subencoding 2-16)
        if 2 <= ncol <= 16:
            pp = self._packed_palette(px, freq, tw, th, cps)
            if len(pp) < len(best):
                best = pp

        # plain RLE (subencoding 128)
        prle = bytes([128]) + self._plain_rle(px, cps)
        if len(prle) < len(best):
            best = prle

        # palette RLE (subencoding 129-255, palette size 1-127)
        if 2 <= ncol <= 127:
            palrle = self._palette_rle(px, freq, cps)
            if len(palrle) < len(best):
                best = palrle

        return best

    # ------------------------------------------------------------------
    # subencoding builders
    # ------------------------------------------------------------------

    @staticmethod
    def _packed_palette(px, freq, w, h, cps):
        palette = list(freq)
        ncol = len(palette)
        idx_map = {c: i for i, c in enumerate(palette)}

        if ncol == 2:
            bpi = 1
        elif ncol <= 4:
            bpi = 2
        else:
            bpi = 4

        packed = bytearray()
        ppb = 8 // bpi                       # pixels per byte
        mask = (1 << bpi) - 1
        for row in range(h):
            accum = 0
            bits = 0
            for col in range(w):
                idx = idx_map[px[row * w + col]]
                accum = (accum << bpi) | (idx & mask)
                bits += bpi
                if bits == 8:
                    packed.append(accum)
                    accum = 0
                    bits = 0
            if bits:
                packed.append(accum << (8 - bits))

        out = bytearray()
        out.append(ncol)                     # subencoding = palette size
        for c in palette:
            out.extend(c)
        out.extend(packed)
        return bytes(out)

    @staticmethod
    def _plain_rle(px, cps):
        out = bytearray()
        i, n = 0, len(px)
        while i < n:
            p = px[i]
            run = 1
            while i + run < n and px[i + run] == p:
                run += 1
            out.extend(p)
            out.extend(Encoding._run_len(run))
            i += run
        return bytes(out)

    @staticmethod
    def _palette_rle(px, freq, cps):
        palette = list(freq)
        ncol = len(palette)
        idx_map = {c: i for i, c in enumerate(palette)}

        out = bytearray()
        out.append(128 + ncol)               # subencoding
        for c in palette:
            out.extend(c)

        i, n = 0, len(px)
        while i < n:
            idx = idx_map[px[i]]
            run = 1
            while i + run < n and idx_map[px[i + run]] == idx:
                run += 1
            if run == 1:
                out.append(idx)              # bit 7 clear → length 1
            else:
                out.append(idx | 0x80)       # bit 7 set → length follows
                out.extend(Encoding._run_len(run))
            i += run
        return bytes(out)

    @staticmethod
    def _run_len(length):
        """Encode run length as 7-bit chunks (value stored = length - 1)."""
        v = length - 1
        out = bytearray()
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                b |= 0x80
            out.append(b)
            if not v:
                break
        return bytes(out)


common.encodings[common.ENCODINGS.zrle] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
