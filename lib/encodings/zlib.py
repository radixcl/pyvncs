from . import common
from struct import *
from lib import log
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
                zlib.Z_DEFAULT_COMPRESSION,        # level: 0-9
                zlib.DEFLATED,        # method: must be DEFLATED
                zlib.MAX_WBITS,                # window size in bits:
                                    #   -15..-8: negate, suppress header
                                    #   8..15: normal
                                    #   16..30: subtract 16, gzip header
                zlib.DEF_MEM_LEVEL,                  # mem level: 1..8/9
                zlib.Z_DEFAULT_STRATEGY            # strategy:
                                    #   0 = Z_DEFAULT_STRATEGY
                                    #   1 = Z_FILTERED
                                    #   2 = Z_HUFFMAN_ONLY
                                    #   3 = Z_RLE
                                    #   4 = Z_FIXED
        )

    def send_image(self, x, y, w, h, image):
        sendbuff = bytearray()

        rectangles = 1
        sendbuff.extend(pack("!BxH", 0, rectangles))    # message type 0 == FramebufferUpdate
        sendbuff.extend(pack("!HHHH", x, y, w, h))
        sendbuff.extend(pack(">i", self.id))

        #log.debug("Compressing...")
        zlibdata = self._compressObj.compress( image.tobytes() )
        zlibdata += self._compressObj.flush(zlib.Z_FULL_FLUSH)
        #log.debug("LEN", len(zlibdata))

        l = pack("!I", len(zlibdata) )
        sendbuff.extend( l )        # send length
        sendbuff.extend( zlibdata ) # send compressed data

        return sendbuff

common.encodings[common.ENCODINGS.zlib] = Encoding

log.debug("Loaded encoding: %s (%s)" % (__name__, Encoding.id))
