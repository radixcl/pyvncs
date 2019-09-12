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
    
    def __quantizetopalette(self, silf, palette, dither=False):
        """Converts an RGB or L mode image to use a given P image's palette."""
        silf.load()

        # use palette from reference image
        palette.load()
        if palette.mode != "P":
            raise ValueError("bad mode for palette image")
        if silf.mode != "RGB" and silf.mode != "L":
            raise ValueError(
                "only RGB or L mode images can be quantized to a palette"
                )
        im = silf.im.convert("P", 1 if dither else 0, palette.im)
        # the 0 above means turn OFF dithering

        # Later versions of Pillow (4.x) rename _makeself to _new
        try:
            return silf._new(im)
        except AttributeError:
            return silf._makeself(im)


    def get_bitmap(self, rectangle):
        if self.bpp == 32:
            redBits = 8 
            greenBits = 8 
            blueBits = 8

            # image array
            a = np.asarray(rectangle).copy()

            redMask = ((1 << redBits) - 1) << self.red_shift
            greenMask = ((1 << greenBits) - 1) << self.green_shift
            blueMask = ((1 << blueBits) - 1) << self.blue_shift
            a[..., 0] = ( a[..., 0] ) & redMask >> self.red_shift
            a[..., 1] = ( a[..., 1] ) & greenMask >> self.green_shift
            a[..., 2] = ( a[..., 2] ) & blueMask >> self.blue_shift

            image = Image.fromarray(a)
            if self.primaryOrder == "rgb":
                (b, g, r) = image.split()
                image = Image.merge("RGB", (r, g, b))
                del b,g,r
            image = image.convert("RGBX")
            return image

        elif self.bpp == 16:  #BGR565
            greenBits = 5
            blueBits = 6
            redBits = 5
            image = rectangle

            if self.primaryOrder == "bgr":  # FIXME: does not work
                (b, g, r) = image.split()
                image = Image.merge("RGB", (r, g, b))

            if self.depth == 16:
                image = image.convert('BGR;16')
            if self.depth == 15:
                image = image.convert('BGR;15')

            return image

        elif self.bpp == 8: #bgr233
            redBits = 3
            greenBits = 3
            blueBits = 2
            image = rectangle

            palette = bgr233_palette.palette
            if self.primaryOrder == "rgb":
                #(b, g, r) = image.split()
                #image = Image.merge("RGB", (r, g, b))

                palette = np.reshape(palette, (-3,3))
                palette[:,[0, 2]] = palette[:,[2, 0]]
                palette = palette.flatten()
                palette = list(palette)

            p = Image.new('P',(16,16))
            p.putpalette(palette)

            image = self.__quantizetopalette(image, p, dither=self.dither)

            #image = image.convert('RGB', colors=4).quantize(palette=p)
            #log.debug(image)
            return image

        else:
            # unsupported BPP
            return None
