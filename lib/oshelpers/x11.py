import ctypes
from ctypes import POINTER, c_int, c_short, c_ushort, c_ulong, c_void_p, Structure, cast
from PIL import Image
import numpy as np

# Definición de Atom para su uso en la estructura XFixesCursorImage
Atom = c_ulong

# Definición de la estructura XFixesCursorImage
class XFixesCursorImage(Structure):
    _fields_ = [
        ("x", c_short),
        ("y", c_short),
        ("width", c_ushort),
        ("height", c_ushort),
        ("xhot", c_ushort),
        ("yhot", c_ushort),
        ("cursor_serial", Atom),
        ("pixels", POINTER(c_ulong)),  # Suponiendo que 'pixels' es un puntero a c_ulong
        ("atom", Atom),  # Presente en la versión 2 y superiores de XFixes
        ("name", ctypes.c_char_p)
    ]


class XCursor:
    def __init__(self):
        # Cargar las bibliotecas X11 y Xfixes
        self.xlib = ctypes.cdll.LoadLibrary("libX11.so")
        self.xfixes = ctypes.cdll.LoadLibrary("libXfixes.so.3")

        # Configurar los tipos de retorno
        self.xlib.XOpenDisplay.restype = POINTER(c_void_p)
        self.xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]

        self.xfixes.XFixesGetCursorImage.restype = POINTER(XFixesCursorImage)
        self.xfixes.XFixesGetCursorImage.argtypes = [c_void_p]

        # Abrir la conexión con X
        self.display = self.xlib.XOpenDisplay(None)
        if not self.display:
            raise Exception("No se pudo abrir el display")
    
    def __del__(self):
        self.xlib.XCloseDisplay(self.display)

    def get_cursor_image(self):
        # Llamar a XFixesGetCursorImage
        cursor_image_ref = self.xfixes.XFixesGetCursorImage(self.display)
        if not cursor_image_ref:
            # return a 2x2 red image
            return Image.fromarray(np.array([[[255, 0, 0, 255], [255, 0, 0, 255]], [[255, 0, 0, 255], [255, 0, 0, 255]]], dtype=np.uint8), 'RGBA')

        cursor_image = cursor_image_ref.contents
        width, height = cursor_image.width, cursor_image.height

        # Leer los datos de píxeles
        pixels_array_type = c_ulong * (cursor_image.width * cursor_image.height)
        pixels_pointer = cast(cursor_image.pixels, POINTER(pixels_array_type))
        pixels_64bit = np.frombuffer(pixels_pointer.contents, dtype=np.uint64)

        # Convertir cada valor de 64 bits en un píxel RGBA
        pixels_rgba = np.zeros((cursor_image.height, cursor_image.width, 4), dtype=np.uint8)

        for i in range(cursor_image.height):
            for j in range(cursor_image.width):
                pixel = int(pixels_64bit[i * cursor_image.width + j])  # Convertir a int para bit shifting
                pixels_rgba[i, j, 0] = (pixel >> 16) & 0xFF  # Rojo
                pixels_rgba[i, j, 1] = (pixel >> 8) & 0xFF   # Verde
                pixels_rgba[i, j, 2] = pixel & 0xFF          # Azul
                pixels_rgba[i, j, 3] = (pixel >> 24) & 0xFF
        
        return Image.fromarray(pixels_rgba, 'RGBA')

