from . import common
from lib import log
import zlib
from struct import pack
from PIL import Image

class Encoding:
    name = 'zrle'
    id = 16
    description = 'zrle VNC encoding'
    enabled = True

    def __init__(self):
        log.debug("Initialized", __name__)
        self._compressObj = zlib.compressobj()


    def send_image(self, x, y, w, h, image):
        sendbuff = bytearray()
        rectangles = 1
        sendbuff.extend(pack("!BxH", 0, rectangles))  # FramebufferUpdate
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))  # ID de encoding ZRLE

        tmpbuf = bytearray()

        # Dividir la imagen en tiles y comprimirlas
        for tile_y in range(0, h, 64):
            for tile_x in range(0, w, 64):
                tile = image.crop((tile_x, tile_y, min(tile_x + 64, w), min(tile_y + 64, h)))
                encoded_tile = self.tile_encode(tile)
                tmpbuf.extend(encoded_tile)

        compressed_data = self._compressObj.compress(tmpbuf)
        compressed_data += self._compressObj.flush(zlib.Z_SYNC_FLUSH)

        sendbuff.extend(pack("!I", len(compressed_data)))
        sendbuff.extend(compressed_data)
        log.debug("zrle send_image", x, y, w, h, image)
        return sendbuff

    def tile_encode(self, tile):
        """Codifica una baldosa (tile) de la imagen usando ZRLE."""
        w, h = tile.size
        pixels = list(tile.getdata())
        rle_data = bytearray()

        # Proceso RLE para la baldosa
        prev_pixel = pixels[0]
        count = 1
        for pixel in pixels[1:]:
            if pixel == prev_pixel and count < 255:
                count += 1
            else:
                rle_data.extend(self._pack_pixel(prev_pixel, count))
                prev_pixel = pixel
                count = 1
        rle_data.extend(self._pack_pixel(prev_pixel, count))

        # Empaquetar la data RLE con el byte de subencoding
        encoded_tile = bytearray()
        encoded_tile.append(128)  # Subencoding RLE
        encoded_tile.extend(rle_data)

        return encoded_tile

    def _pack_pixel(self, pixel, count):
        if isinstance(pixel, tuple):
            # RGBA
            r, g, b, a = pixel
            pixel_data = bytes([r, g, b])  # Usar solo RGB para ZRLE.
        else:
            pixel_data = bytes([pixel, pixel, pixel])

        count_data = self._encode_run_length(count)
        return pixel_data + count_data

    def _encode_run_length(self, length):
        """Codifica la longitud de una secuencia para RLE."""
        if length == 1:
            return b""
        length -= 1  # La longitud se incrementa en 1 según el protocolo ZRLE
        encoded_length = bytearray()
        while length > 0:
            byte = length % 255
            encoded_length.append(byte)
            length //= 255
            if length > 0:
                encoded_length.append(255)
        return encoded_length


common.encodings[common.ENCODINGS.zrle] = Encoding

log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
