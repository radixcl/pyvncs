from . import common
from lib import log
from struct import *
import zlib
from io import BytesIO
import numpy as np

class Encoding:
    name = 'tight'
    id = 7
    description = 'Tight VNC encoding'
    enabled = True
    use_jpeg_threshold = 0  # color threshold to use jpeg compression, 0 = always use jpeg
    jpeg_compression_quality = 75

    def __init__(self):
        self.compress_obj = zlib.compressobj(
            zlib.Z_DEFAULT_COMPRESSION,
            zlib.DEFLATED,
            zlib.MAX_WBITS,
            zlib.DEF_MEM_LEVEL,
            zlib.Z_DEFAULT_STRATEGY
        )
        self._last_compression_was_jpeg = False

    def send_image(self, x, y, w, h, image):
        sendbuff = bytearray()

        if image.mode == 'RGBX' or image.mode == 'RGBA':
            image = image.convert('RGB')

        rectangles = 1
        sendbuff.extend(pack("!BxH", 0, rectangles))  # FramebufferUpdate
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        if self._should_use_jpeg(image, self.use_jpeg_threshold):
            self._last_compression_was_jpeg = True
            compressed_data = self._compress_image_jpeg(image, self.jpeg_compression_quality)
            sendbuff.append(0x90)  # 0x90 = 10010000 = JPEG subencoding
        else:
            compressed_data = self._compress_image_zlib(image)
            sendbuff.append(0)  # control byte
        
        # content lenght
        sendbuff.extend(self._send_compact_length(len(compressed_data)))

        # Tight data
        sendbuff.extend(compressed_data)

        return sendbuff

    def _send_compact_length(self, length):
        sendbuff = bytearray()
        while True:
            # Lowest 7 bits of length
            byte = length & 0x7F
            length >>= 7
            # if more length bytes are required, set the next highest bit
            if length > 0:
                byte |= 0x80
            sendbuff.append(byte)
            if length == 0:
                break
        return sendbuff

    def _should_use_jpeg(self, image, threshold=256):
        if image.mode == 'P':
            return False
        
        if self.use_jpeg_threshold == 0:
            return True

        if image.mode == 'RGB':
            width, height = image.size
            sample_size = min(width * height, 1000)
            sample = np.array(image).reshape(-1, 3)[:sample_size]
            unique_colors = np.unique(sample, axis=0)
            return len(unique_colors) > threshold

        return False

    def _compress_image_jpeg(self, image, quality=75):
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        jpeg_data = buffer.getvalue()
        buffer.close()
        return jpeg_data

    def _compress_image_zlib(self, image):
        if self.compress_obj is None or self._last_compression_was_jpeg:
            self.compress_obj = zlib.compressobj(
                zlib.Z_DEFAULT_COMPRESSION,
                zlib.DEFLATED,
                -zlib.MAX_WBITS,  # suppress zlib header
                zlib.DEF_MEM_LEVEL,
                zlib.Z_DEFAULT_STRATEGY
            )
            self._last_compression_was_jpeg = False

        zlib_data = self.compress_obj.compress(image.tobytes()) + self.compress_obj.flush(zlib.Z_SYNC_FLUSH)
        return zlib_data


common.encodings[common.ENCODINGS.tight] = Encoding
log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
