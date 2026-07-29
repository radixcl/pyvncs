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
        self._compression_level = 1
        self._comp = self._new_stream()

    def _new_stream(self):
        return zlib.compressobj(
            self._compression_level, zlib.DEFLATED,
            zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)
        # rfb_bitmap has already swapped R↔B when primaryOrder == "bgr"
        # (the case for little-endian rgb888), so arr is already in wire
        # order (byte 0 = B, byte 2 = R).  Re-swapping here — as the
        # previous version did — exchanges red and blue on screen.
        # No further channel swap is needed.

        cps = self._cpixel_size(bpp, depth)
        tile_data = bytearray()

        for ty in range(0, h, self.TILE):
            for tx in range(0, w, self.TILE):
                tw = min(self.TILE, w - tx)
                th = min(self.TILE, h - ty)
                tile = arr[ty:ty + th, tx:tx + tw]
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

    @staticmethod
    def _to_cpixels(arr, cps):
        if cps == 3:
            return np.ascontiguousarray(arr[:, :, :3])
        if cps == 2:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            val = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out = np.empty(arr.shape[:2] + (2,), dtype=np.uint8)
            out[:, :, 0] = val & 0xFF
            out[:, :, 1] = (val >> 8) & 0xFF
            return out
        return np.ascontiguousarray(arr)

    # ------------------------------------------------------------------
    # tile encoding
    # ------------------------------------------------------------------

    def _encode_tile(self, tile, cps, bpp, depth):
        pixels = self._to_cpixels(tile, cps)
        h, w = pixels.shape[:2]
        n = w * h

        flat = pixels.reshape(-1, cps)
        dt = np.dtype((np.void, cps))
        flat_s = np.ascontiguousarray(flat).view(dt).ravel()
        uniq_s, inv = np.unique(flat_s, return_inverse=True)
        ncol = len(uniq_s)

        if ncol == 1:
            return bytes([1]) + uniq_s[0].tobytes()

        best = None

        raw = bytes([0]) + flat.tobytes()
        best = raw

        if 2 <= ncol <= 16:
            pp = self._packed_palette(inv, uniq_s, w, h, cps, ncol)
            if len(pp) < len(best):
                best = pp

        prle = self._plain_rle(flat_s, cps)
        if len(prle) < len(best):
            best = prle

        if 2 <= ncol <= 127:
            palrle = self._palette_rle(inv, uniq_s, cps, ncol)
            if len(palrle) < len(best):
                best = palrle

        return best

    # ------------------------------------------------------------------
    # subencoding builders
    # ------------------------------------------------------------------

    @staticmethod
    def _packed_palette(inv, uniq_s, w, h, cps, ncol):
        if ncol == 2:
            bpi = 1
        elif ncol <= 4:
            bpi = 2
        else:
            bpi = 4

        idx = inv.reshape(h, w).astype(np.uint8)
        ppb = 8 // bpi
        pad_w = ((w + ppb - 1) // ppb) * ppb

        if bpi == 1:
            padded = np.zeros((h, pad_w), dtype=np.uint8)
            padded[:, :w] = idx & 1
            packed = np.packbits(padded, axis=1).tobytes()
        elif bpi == 2:
            padded = np.zeros((h, pad_w), dtype=np.uint8)
            padded[:, :w] = idx & 3
            rows = padded.reshape(h, -1, 4)
            packed_bytes = (rows[:, :, 0] << 6) | (rows[:, :, 1] << 4) | (rows[:, :, 2] << 2) | rows[:, :, 3]
            packed = packed_bytes.tobytes()
        else:
            padded = np.zeros((h, pad_w), dtype=np.uint8)
            padded[:, :w] = idx & 0xF
            rows = padded.reshape(h, -1, 2)
            packed_bytes = (rows[:, :, 0] << 4) | rows[:, :, 1]
            packed = packed_bytes.tobytes()

        out = bytearray()
        out.append(ncol)
        for u in uniq_s:
            out.extend(u.tobytes())
        out.extend(packed)
        return bytes(out)

    @staticmethod
    def _run_len_bytes(length):
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
        return out

    @staticmethod
    def _run_len(length):
        return bytes(Encoding._run_len_bytes(length))

    @staticmethod
    def _plain_rle(flat_s, cps):
        out = bytearray()
        out.append(128)
        n = len(flat_s)
        if n == 0:
            return bytes(out)
        arr = np.asarray(flat_s).view(np.uint8).reshape(n, cps)
        if cps == 1:
            vals = arr[:, 0]
            changes = np.flatnonzero(vals[1:] != vals[:-1]) + 1
        else:
            changes = np.flatnonzero(np.any(arr[1:] != arr[:-1], axis=1)) + 1
        starts = np.concatenate(([0], changes))
        ends = np.concatenate((changes, [n]))
        lengths = ends - starts
        for i in range(len(starts)):
            out.extend(arr[starts[i]].tobytes())
            out.extend(Encoding._run_len_bytes(int(lengths[i])))
        return bytes(out)

    @staticmethod
    def _palette_rle(inv, uniq_s, cps, ncol):
        out = bytearray()
        out.append(128 + ncol)
        for u in uniq_s:
            out.extend(u.tobytes())

        n = len(inv)
        if n == 0:
            return bytes(out)
        idx = np.asarray(inv, dtype=np.int32)
        changes = np.flatnonzero(idx[1:] != idx[:-1]) + 1
        starts = np.concatenate(([0], changes))
        ends = np.concatenate((changes, [n]))
        lengths = ends - starts
        for i in range(len(starts)):
            v = int(idx[starts[i]])
            run = int(lengths[i])
            if run == 1:
                out.append(v)
            else:
                out.append(v | 0x80)
                out.extend(Encoding._run_len_bytes(run))
        return bytes(out)


common.encodings[common.ENCODINGS.zrle] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
