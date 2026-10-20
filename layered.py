"""Present one antialiased RGBA frame and its final bounds in a single call."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BlendFunction(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE)]


def premultiplied_bgra(image: Image.Image) -> bytes:
    """Windows AC_SRC_ALPHA requires premultiplied color, not straight RGBA."""
    return image.convert("RGBA").convert("RGBa").tobytes("raw", "BGRa")


class LayeredPresenter:
    def __init__(self, root):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self._declare_apis()
        # The initial Tk wrapper is created while withdrawn. No partial frame
        # becomes visible while the compositor and the first bitmap are prepared.
        root.update_idletasks()
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        style = self.user.GetWindowLongW(self.hwnd, -20)
        self.user.SetWindowLongW(self.hwnd, -20, style | 0x00080000)  # WS_EX_LAYERED
        self.dc = self.gdi.CreateCompatibleDC(None)
        if not self.dc:
            raise ctypes.WinError(ctypes.get_last_error())
        self.bitmap = None
        self.previous_bitmap = None
        self.bits = ctypes.c_void_p()
        self.size = None
        self.opacity = 255
        self.frames = 0
        self.last_bounds = None

    def _declare_apis(self):
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongW.restype = wintypes.LONG
        self.user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        self.user.SetWindowLongW.restype = wintypes.LONG
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowRect.restype = wintypes.BOOL
        self.user.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
            ctypes.POINTER(BlendFunction), wintypes.DWORD]
        self.user.UpdateLayeredWindow.restype = wintypes.BOOL
        self.gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi.CreateDIBSection.argtypes = [wintypes.HDC,
            ctypes.POINTER(BitmapInfoHeader), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        self.gdi.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi.SelectObject.restype = wintypes.HANDLE
        self.gdi.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi.DeleteObject.restype = wintypes.BOOL
        self.gdi.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi.DeleteDC.restype = wintypes.BOOL

    def _resize(self, size):
        if size == self.size:
            return
        width, height = size
        info = BitmapInfoHeader()
        info.biSize = ctypes.sizeof(info)
        info.biWidth, info.biHeight = width, -height  # top-down BGRA
        info.biPlanes, info.biBitCount = 1, 32
        info.biSizeImage = width * height * 4
        bits = ctypes.c_void_p()
        bitmap = self.gdi.CreateDIBSection(self.dc, ctypes.byref(info), 0,
                                         ctypes.byref(bits), None, 0)
        if not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        previous = self.gdi.SelectObject(self.dc, bitmap)
        if not previous or previous == ctypes.c_void_p(-1).value:
            self.gdi.DeleteObject(bitmap)
            raise ctypes.WinError(ctypes.get_last_error())
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        else:
            self.previous_bitmap = previous
        self.bitmap, self.bits, self.size = bitmap, bits, size

    def present(self, image: Image.Image, x: int, y: int):
        self._resize(image.size)
        pixels = premultiplied_bgra(image)
        ctypes.memmove(self.bits, pixels, len(pixels))
        destination, source = wintypes.POINT(x, y), wintypes.POINT(0, 0)
        size = wintypes.SIZE(*image.size)
        blend = BlendFunction(0, 0, self.opacity, 1)  # AC_SRC_OVER / AC_SRC_ALPHA
        if not self.user.UpdateLayeredWindow(self.hwnd, None,
                ctypes.byref(destination), ctypes.byref(size), self.dc,
                ctypes.byref(source), 0, ctypes.byref(blend), 2):  # ULW_ALPHA
            raise ctypes.WinError(ctypes.get_last_error())
        self.frames += 1
        self.last_bounds = (x, y, *image.size)

    def bounds(self):
        rectangle = wintypes.RECT()
        if not self.user.GetWindowRect(self.hwnd, ctypes.byref(rectangle)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (rectangle.left, rectangle.top, rectangle.right-rectangle.left,
                rectangle.bottom-rectangle.top)

    def close(self):
        if self.dc:
            if self.bitmap:
                self.gdi.SelectObject(self.dc, self.previous_bitmap)
                self.gdi.DeleteObject(self.bitmap)
                self.bitmap = None
            self.gdi.DeleteDC(self.dc)
            self.dc = None
