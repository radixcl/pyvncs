import sys
from PIL import Image
from lib import log

if sys.platform == "linux" or sys.platform == "linux2":
    log.debug("ImageGrab: running on Linux")
    from Xlib import display, X
    # take screen images, that's not the best way, so here
    # we use directly use xlib to take the screenshot.
    class ImageGrab():
        @staticmethod
        def grab():
            dsp = display.Display()
            root = dsp.screen().root
            geom = root.get_geometry()
            w = geom.width
            h = geom.height
            raw = root.get_image(0, 0, w ,h, X.ZPixmap, 0xffffffff)
            image = Image.frombytes("RGB", (w, h), raw.data, "raw", "BGRX")
            return image

elif sys.platform == "darwin":
    log.debug("ImageGrab: running on darwin")
    import Quartz.CoreGraphics as CG
    class ImageGrab():
        @staticmethod
        def grab():
            screenshot = CG.CGWindowListCreateImage(CG.CGRectInfinite, CG.kCGWindowListOptionOnScreenOnly, CG.kCGNullWindowID, CG.kCGWindowImageDefault)
            width = CG.CGImageGetWidth(screenshot)
            height = CG.CGImageGetHeight(screenshot)
            bytesperrow = CG.CGImageGetBytesPerRow(screenshot)

            pixeldata = CG.CGDataProviderCopyData(CG.CGImageGetDataProvider(screenshot))

            i = Image.frombytes("RGBA", (width, height), pixeldata)
            (b, g, r, x) = i.split()
            i = Image.merge("RGBX", (r, g, b, x))

            return i

else:
    log.debug("ImageGrab: running on Unknown!")
    from PIL import ImageGrab
