from . import common
from lib import log
import zlib
import numpy as np
from struct import pack
from io import BytesIO


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

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def send_image(self, x, y, w, h, image, bpp=32, depth=24):
        sendbuff = bytearray()
        sendbuff.extend(pack("!BxH", 0, 1))
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        tps = self._tpixel_size(bpp, depth)
        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr.reshape(arr.shape[0], arr.shape[1], 1)

        # unique-colour count drives the filter choice
        flat = arr.reshape(-1, arr.shape[2])
        ncol = len(np.unique(flat, axis=0))

        if ncol == 1:
            sendbuff.extend(self._fill(arr, tps))
        elif bpp >= 24 and self._want_jpeg(arr, w, h):
            sendbuff.extend(self._jpeg(image))
        elif ncol <= 256:
            sendbuff.extend(self._palette(arr, tps, bpp, depth))
        else:
            sendbuff.extend(self._best_of_gradient_copy(arr, tps, bpp, depth))

        return sendbuff

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

    def _jpeg(self, image):
        if image.mode != 'RGB':
            image = image.convert('RGB')
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
    # filter: palette  (explicit 0x4_, filter-id 1)
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

        # pack indices
        if ncol <= 2:
            bpi = 1
        elif ncol <= 4:
            bpi = 2
        elif ncol <= 16:
            bpi = 4
        else:
            bpi = 8

        if bpi == 8:
            packed = idx.tobytes()
        else:
            ppb = 8 // bpi
            mask = (1 << bpi) - 1
            packed = bytearray()
            for row in range(h):
                for c0 in range(0, w, ppb):
                    byte = 0
                    for k in range(ppb):
                        c = c0 + k
                        if c < w:
                            byte |= int(idx[row, c] & mask) << (8 - bpi * (k + 1))
                    packed.append(byte)
            packed = bytes(packed)

        comp = self._compress(packed, self._S_PALETTE)

        out = bytearray()
        out.append((self._EXPLICIT << 4) | (1 << self._S_PALETTE))
        out.append(self._PALETTE)
        out.append(ncol)
        for c in uniq:
            out.extend(bytes(c[:tps]))
        out.extend(self._compact_len(len(comp)))
        out.extend(comp)
        return bytes(out)

    # ------------------------------------------------------------------
    # filter: gradient  (explicit 0x4_, filter-id 2)
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

        comp = self._compress(diff.tobytes(), self._S_GRADIENT)

        out = bytearray()
        out.append((self._EXPLICIT << 4) | (1 << self._S_GRADIENT))
        out.append(self._GRADIENT)
        out.extend(self._compact_len(len(comp)))
        out.extend(comp)
        return bytes(out)

    # ------------------------------------------------------------------
    # filter: copy  (explicit 0x4_, filter-id 0)
    # ------------------------------------------------------------------

    def _copy(self, arr, tps, bpp, depth):
        raw = self._to_tpixels(arr, bpp, depth)
        comp = self._compress(raw, self._S_COPY)

        out = bytearray()
        out.append((self._EXPLICIT << 4) | (1 << self._S_COPY))
        out.append(self._COPY)
        out.extend(self._compact_len(len(comp)))
        out.extend(comp)
        return bytes(out)

    # ------------------------------------------------------------------
    # pick best of gradient / copy
    # ------------------------------------------------------------------

    def _best_of_gradient_copy(self, arr, tps, bpp, depth):
        g = self._gradient(arr, tps, bpp, depth)
        c = self._copy(arr, tps, bpp, depth)
        return g if len(g) <= len(c) else c


common.encodings[common.ENCODINGS.tight] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
