from . import common
from lib import log
import zlib
import numpy as np
from struct import pack
from io import BytesIO
from PIL import Image


class Encoding:
    """Tight encoding (RFC 6143 §7.6.7 / TightVNC spec).

    Compression control byte layout
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Bits 0-3  stream-reset flags  (bit *i* → reset zlib stream *i*)
    Bits 4-7  compression type
        0x4  explicit filter  (next byte = filter-id)
        0x8  fill            (single TPIXEL follows)
        0x9  JPEG            (compact-length + JPEG data follows)

    Filter ids (after 0x4 control byte)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    0  CopyFilter     – raw TPIXELs, zlib stream 0
    1  PaletteFilter  – palette + packed indices, zlib stream 1
    2  GradientFilter – gradient-predicted diffs, zlib stream 2

    TPIXEL is 3 bytes for 32 bpp / depth 24, otherwise bytesPerPixel.
    """

    name = 'tight'
    id = 7
    description = 'Tight VNC encoding'
    enabled = True
    framebuffer = None
    firstUpdateSent = False

    # compression-type nibble values (stored in bits 4-7)
    _EXPLICIT = 0x04
    _FILL     = 0x08
    _JPEG     = 0x09

    # filter ids
    _COPY     = 0
    _PALETTE  = 1
    _GRADIENT = 2

    # stream assignments
    _S_COPY     = 0
    _S_PALETTE  = 1
    _S_GRADIENT = 2

    def __init__(self):
        log.debug("Initialized", __name__)
        self._streams = [self._new_stream() for _ in range(4)]
        self._jpeg_quality = 75

    @staticmethod
    def _new_stream():
        return zlib.compressobj(
            zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED,
            zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY)

    # Tight decoders (libvncclient, TigerVNC) reject rects wider/taller
    # than this.  We tile large updates into a grid of strips.
    MAX_RECT = 2048

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        # Split into tiles that fit within MAX_RECT
        tiles = []
        for ty in range(0, h, self.MAX_RECT):
            th = min(self.MAX_RECT, h - ty)
            for tx in range(0, w, self.MAX_RECT):
                tw = min(self.MAX_RECT, w - tx)
                tiles.append((x + tx, y + ty, tw, th,
                              image.crop((tx, ty, tx + tw, ty + th))))

        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, len(tiles)))

        for rx, ry, rw, rh, tile in tiles:
            sendbuff.extend(pack("!HHHH", rx, ry, rw, rh))
            sendbuff.extend(pack(">i", self.id))
            sendbuff.extend(self._encode_tile(tile, rw, rh, bpp, depth))

        return sendbuff

    def _encode_tile(self, image, w, h, bpp, depth):
        """Encode a single tile (already ≤ MAX_RECT in each dimension)."""
        tps = self._tpixel_size(bpp, depth)
        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)

        # rfb_bitmap swaps R↔B for "bgr" primaryOrder (needed by raw/zlib
        # which pack pixels with the client's shift values).  TPIXEL is
        # always plain RGB per the Tight spec, so undo the swap here.
        if getattr(self, 'primaryOrder', 'rgb') == 'bgr' and arr.shape[2] >= 3:
            arr = arr[:, :, [2, 1, 0]]

        flat = arr.reshape(-1, arr.shape[2])
        ncol = len(np.unique(flat, axis=0))

        if ncol == 1:
            return self._fill(arr, tps)
        if bpp >= 24 and self._want_jpeg(arr, w, h):
            return self._jpeg_from_arr(arr)
        if ncol <= 256:
            return self._palette(arr, tps, bpp, depth)
        return self._many_colours(arr, tps, bpp, depth)

    # ------------------------------------------------------------------
    # TPIXEL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tpixel_size(bpp, depth):
        if bpp == 32 and depth == 24:
            return 3
        return max(bpp // 8, 1)

    @staticmethod
    def _to_tpixels(arr, bpp, depth):
        tps = Encoding._tpixel_size(bpp, depth)
        if tps == 3:
            return arr[:, :, :3].tobytes()
        if tps == 2:
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            v = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out = np.empty(arr.shape[:2] + (2,), dtype=np.uint8)
            out[:, :, 0] = v & 0xFF
            out[:, :, 1] = (v >> 8) & 0xFF
            return out.tobytes()
        return arr.tobytes()

    # ------------------------------------------------------------------
    # compact length (Tight-specific variable-length integer)
    # ------------------------------------------------------------------

    @staticmethod
    def _compact_len(n):
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                b |= 0x80
            out.append(b)
            if not n:
                break
        return bytes(out)

    # ------------------------------------------------------------------
    # zlib helpers
    # ------------------------------------------------------------------

    def _compress(self, data, sid):
        s = self._streams[sid]
        out = s.compress(data)
        out += s.flush(zlib.Z_SYNC_FLUSH)
        return out

    # ------------------------------------------------------------------
    # filter: fill  (control 0x8_)
    # ------------------------------------------------------------------

    def _fill(self, arr, tps):
        out = bytearray()
        out.append(self._FILL << 4)
        out.extend(arr[0, 0, :tps].tobytes())
        return bytes(out)

    # ------------------------------------------------------------------
    # filter: JPEG  (control 0x9_)
    # ------------------------------------------------------------------

    def _jpeg_from_arr(self, arr):
        # arr is already RGB (swap undone in _encode_tile)
        image = Image.fromarray(arr[:, :, :3].astype(np.uint8))
        buf = BytesIO()
        image.save(buf, format='JPEG', quality=self._jpeg_quality)
        jpg = buf.getvalue()

        out = bytearray()
        out.append(self._JPEG << 4)
        out.extend(self._compact_len(len(jpg)))
        out.extend(jpg)
        return bytes(out)

    def _want_jpeg(self, arr, w, h):
        if w * h < 4096:
            return False
        flat = arr.reshape(-1, arr.shape[2])
        sample = flat[:min(len(flat), 2048)]
        return len(np.unique(sample, axis=0)) > 96

    # ------------------------------------------------------------------
    # filter: palette  (explicit, filter-id 1, stream 1)
    # ------------------------------------------------------------------

    def _palette(self, arr, tps, bpp, depth):
        h, w = arr.shape[:2]
        flat = arr.reshape(-1, arr.shape[2])

        # unique colours → palette
        uniq = np.unique(flat, axis=0)
        ncol = len(uniq)

        # vectorised index lookup via structured-array searchsorted
        dt = np.dtype((np.void, flat.shape[1] * flat.dtype.itemsize))
        flat_s = np.ascontiguousarray(flat).view(dt).ravel()
        uniq_s = np.ascontiguousarray(uniq).view(dt).ravel()
        idx = np.searchsorted(uniq_s, flat_s).astype(np.uint8).reshape(h, w)

        # Spec: 1 bit/pixel for 2 colours, 8 bits/pixel otherwise.
        # (libvncclient / TigerVNC decoders only support 1 or 8.)
        if ncol == 2:
            packed = bytearray()
            for row in range(h):
                byte = 0
                for col in range(w):
                    if col % 8 == 0 and col > 0:
                        packed.append(byte)
                        byte = 0
                    byte |= int(idx[row, col] & 1) << (7 - (col % 8))
                packed.append(byte)
            packed = bytes(packed)
        else:
            packed = idx.tobytes()

        # Spec: if uncompressed size < 12, send raw (no zlib)
        if len(packed) < 12:
            comp = None
        else:
            comp = self._compress(packed, self._S_PALETTE)

        out = bytearray()
        # control byte: BasicCompression, read-filter-id, stream 1
        out.append((self._EXPLICIT << 4) | (self._S_PALETTE << 4))
        out.append(self._PALETTE)
        out.append(ncol - 1)  # spec: "number of colours minus one"
        for c in uniq:
            out.extend(bytes(c[:tps]))
        if comp is not None:
            out.extend(self._compact_len(len(comp)))
            out.extend(comp)
        else:
            out.extend(packed)
        return bytes(out)

    # ------------------------------------------------------------------
    # filter: gradient  (explicit, filter-id 2, stream 2)
    # ------------------------------------------------------------------

    def _gradient(self, arr, tps, bpp, depth):
        h, w = arr.shape[:2]
        ch = min(arr.shape[2], tps)
        src = arr[:, :, :ch].astype(np.int16)

        left  = np.zeros_like(src); left[:, 1:]  = src[:, :-1]
        above = np.zeros_like(src); above[1:, :] = src[:-1, :]
        ul    = np.zeros_like(src); ul[1:, 1:]   = src[:-1, :-1]

        pred = np.clip(left + above - ul, 0, 255).astype(np.uint8)
        diff = ((src.astype(np.int16) - pred.astype(np.int16)) & 0xFF).astype(np.uint8)
        raw = diff.tobytes()

        out = bytearray()
        # control byte: BasicCompression, read-filter-id, stream 2
        out.append((self._EXPLICIT << 4) | (self._S_GRADIENT << 4))
        out.append(self._GRADIENT)
        if len(raw) < 12:
            out.extend(raw)
        else:
            comp = self._compress(raw, self._S_GRADIENT)
            out.extend(self._compact_len(len(comp)))
            out.extend(comp)
        return bytes(out)

    # ------------------------------------------------------------------
    # filter: copy  (explicit, filter-id 0, stream 0)
    # ------------------------------------------------------------------

    def _copy(self, arr, tps, bpp, depth):
        raw = self._to_tpixels(arr, bpp, depth)

        out = bytearray()
        # control byte: BasicCompression, read-filter-id, stream 0
        out.append((self._EXPLICIT << 4) | (self._S_COPY << 4))
        out.append(self._COPY)
        if len(raw) < 12:
            out.extend(raw)
        else:
            comp = self._compress(raw, self._S_COPY)
            out.extend(self._compact_len(len(comp)))
            out.extend(comp)
        return bytes(out)

    # ------------------------------------------------------------------
    # >256 colours: gradient filter (stream 2)
    # ------------------------------------------------------------------
    # NOTE: we must NOT compress into both stream 0 and stream 2 to
    # "pick the smallest" — the unused stream would accumulate state
    # the client never sees, desyncing zlib and causing inflate errors
    # on the next update.  Gradient is almost always smaller than raw
    # copy for complex images, so we use it unconditionally.

    def _many_colours(self, arr, tps, bpp, depth):
        return self._gradient(arr, tps, bpp, depth)


common.encodings[common.ENCODINGS.tight] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
