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
            if self.primaryOrder == "bgr":
                (r, g, b) = image.split()
                image = Image.merge("RGB", (b, g, r))
                del b, g, r

            a = np.array(image)
            r = (a[..., 0] >> 5) & 0x07
            g = (a[..., 1] >> 2) & 0x07
            b = (a[..., 2] >> 5) & 0x07
            bgr233 = (b << 6) | (g << 3) | r
            image = Image.fromarray(bgr233.astype('uint8'), 'P')
            image.putpalette(bgr233_palette.palette)
            return image

        else:
            return None
