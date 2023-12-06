import sys
from PIL import Image
from lib import log

class ImageGrab():
    @staticmethod
    def grab():
        if sys.platform == "linux" or sys.platform == "linux2":
            from Xlib import display, X
            dsp = display.Display()
            root = dsp.screen().root
            geom = root.get_geometry()
            w = geom.width
            h = geom.height
            raw = root.get_image(0, 0, w ,h, X.ZPixmap, 0xffffffff)
            image = Image.frombytes("RGB", (w, h), raw.data, "raw", "BGRX")
            return image

        elif sys.platform == "darwin":
            import Quartz.CoreGraphics as CG
            screenshot = CG.CGWindowListCreateImage(CG.CGRectInfinite, CG.kCGWindowListOptionOnScreenOnly, CG.kCGNullWindowID, CG.kCGWindowImageDefault)
            width = CG.CGImageGetWidth(screenshot)
            height = CG.CGImageGetHeight(screenshot)
            bytesperrow = CG.CGImageGetBytesPerRow(screenshot)

            pixeldata = CG.CGDataProviderCopyData(CG.CGImageGetDataProvider(screenshot))

            i = Image.frombytes("RGBA", (width, height), pixeldata)
            (b, g, r, x) = i.split()
            i = Image.merge("RGBX", (r, g, b, x))

            return i

        elif sys.platform == "win32":
            from PIL import ImageGrab as WinImageGrab
            return WinImageGrab.grab()

        else:
            log.debug("ImageGrab: running on an unknown platform!")
            raise EnvironmentError("Unsupported platform")
