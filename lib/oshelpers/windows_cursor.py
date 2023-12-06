import ctypes
import ctypes.wintypes
from PIL import Image
import numpy as np

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD)
    ]

class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte)
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.wintypes.BOOL),
        ("xHotspot", ctypes.wintypes.DWORD),
        ("yHotspot", ctypes.wintypes.DWORD),
        ("hbmMask", ctypes.wintypes.HBITMAP),
        ("hbmColor", ctypes.wintypes.HBITMAP),
    ]

class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("ptScreenPos", ctypes.wintypes.POINT),
    ]

def get_cursor_image():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci))

    ii = ICONINFO()
    ctypes.windll.user32.GetIconInfo(ci.hCursor, ctypes.byref(ii))

    hdc = ctypes.windll.user32.GetDC(0)  # Usar 0 en lugar de None
    hbmp = ctypes.wintypes.HANDLE(ii.hbmColor)  # Asegurarse de que hbmp es un HANDLE
    bmpinfo = BITMAPINFO()
    bmpinfo.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    ctypes.windll.gdi32.GetDIBits(hdc, hbmp, 0, 0, None, ctypes.byref(bmpinfo), 0)

    width, height = bmpinfo.bmiHeader.biWidth, bmpinfo.bmiHeader.biHeight
    bmpinfo.bmiHeader.biCompression = 0  # BI_RGB
    buffer = ctypes.create_string_buffer(width * height * 4)
    ctypes.windll.gdi32.GetDIBits(hdc, hbmp, 0, height, buffer, ctypes.byref(bmpinfo), 0)

    img = np.frombuffer(buffer, dtype=np.uint8)
    img = img.reshape((height, width, 4))
    img = np.flip(img, axis=0)  # Las imágenes de bitmap en Windows están al revés
    img = Image.fromarray(img, 'RGBA')

    # Free resources
    try:
        ctypes.windll.gdi32.DeleteObject(hbmp)
        ctypes.windll.gdi32.DeleteObject(ii.hbmMask)
        ctypes.windll.user32.ReleaseDC(None, hdc)
    except:
        pass

    return img

