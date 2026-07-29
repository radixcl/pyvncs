import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImagePalette
from lib import bgr233_palette

__all__ = ['RfbBitmap']

class RfbBitmap():

    def __init__(self):
        self.bpp = None
        self.depth = None
        self.truecolor = None
        self.primaryOrder = 'rgb'
        self.dither = False
        self.red_shift = None
        self.green_shift = None
        self.blue_shift = None
        self.bigendian = 0

    def get_bitmap(self, rectangle):
        a = np.asarray(rectangle).copy()

        if self.bpp == 32:
            # Input is an RGB (H,W,3) array; just copy channels as-is.
            # Bitmask/shift logic below only applies to pre-packed XRGB arrays.
            image = Image.fromarray(a)
            if image.mode == "RGBA":
                (r, g, b, a) = image.split()
                image = Image.merge("RGB", (r, g, b))
                del r, g, b, a

            # primaryOrder controls channel order for the encoder
            # rgb = encoder expects RGB, bgr = encoder expects BGR
            if self.primaryOrder == "bgr":
                (r, g, b) = image.split()
                image = Image.merge("RGB", (b, g, r))
                del r, g, b

            return image

        elif self.bpp == 16:
            image = Image.fromarray(a)
            if image.mode == "RGBA":
                (r, g, b, a) = image.split()
                image = Image.merge("RGB", (r, g, b))
                del r, g, b, a

            if self.primaryOrder == "bgr":
                (r, g, b) = image.split()
                image = Image.merge("RGB", (b, g, r))
                del r, g, b

            return image

        elif self.bpp == 8:
            image = rectangle.convert('RGB')
            a = np.array(image).astype(np.uint16)

            r_max = getattr(self, 'red_maximum', 7) or 7
            g_max = getattr(self, 'green_maximum', 7) or 7
            b_max = getattr(self, 'blue_maximum', 3) or 3
            r_shift = getattr(self, 'red_shift', 0) or 0
            g_shift = getattr(self, 'green_shift', 3) or 3
            b_shift = getattr(self, 'blue_shift', 6) or 6

            rq = (a[..., 0] * r_max // 255).astype(np.uint8)
            gq = (a[..., 1] * g_max // 255).astype(np.uint8)
            bq = (a[..., 2] * b_max // 255).astype(np.uint8)

            packed = (rq << r_shift) | (gq << g_shift) | (bq << b_shift)
            image = Image.fromarray(packed.astype('uint8'), 'P')
            image.putpalette(bgr233_palette.palette)
            return image

        else:
            return None
