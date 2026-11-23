"""三选一时卡片下方的「★ 推荐」玻璃胶囊：分层窗口（逐像素透明）+ 点击穿透 + 不抢焦点。

玻璃底是身后游戏画面的模糊截图（游戏画面本来就在截），再盖一层墨黑薄色、顶部高光、渐变描边。
做法同 我的AI Bot 的灵动岛（bot/island.py），这里只要一颗静态胶囊，所以简单很多。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 自己的一份 DLL 对象：下面改 argtypes / restype 不会影响别的模块（windll.user32 是全进程共用的，
# 之前在共用对象上改了 CreateCompatibleDC 的返回类型，screen.py 截图就报 OverflowError）
user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_void_p, ctypes.c_void_p, wt.HDC, ctypes.c_void_p,
                                       wt.COLORREF, ctypes.c_void_p, wt.DWORD]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WM_APP, WM_DESTROY = 0x8000, 0x0002
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
AMBER = (255, 179, 64)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


def render_pill(text: str, backdrop: Image.Image | None, scale: float = 1.0) -> Image.Image:
    """画一颗玻璃胶囊（RGBA）。backdrop = 胶囊身后那块游戏画面。"""
    s = scale
    font = ImageFont.truetype(FONT_BOLD, round(17 * s))
    tw = ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font)
    pad, m = round(18 * s), round(10 * s)            # 内边距、四周留给阴影
    w, h = round(tw + pad * 2), round(40 * s)
    W, H = w + m * 2, h + m * 2
    k = 3                                             # 3 倍大画再缩小，边缘抗锯齿
    shape = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(shape).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=h * k // 2, fill=255)
    shape = shape.resize((w, h), Image.LANCZOS)
    # 玻璃：模糊的身后画面 + 墨黑薄色 + 顶部光
    if backdrop is not None:
        bg = backdrop.convert("RGB").resize((w, h)).filter(ImageFilter.GaussianBlur(6 * s))
        col = np.asarray(bg, np.float32)
    else:
        col = np.full((h, w, 3), 60, np.float32)
    col = col * 0.42 + np.array([16, 16, 20], np.float32) * 0.58
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    col += (1 - yy) ** 2 * 22
    glass = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8), "RGB")
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # 阴影
    sh = Image.new("L", (W, H), 0)
    sh.paste(shape.point(lambda v: v * 0.55), (m, m + round(3 * s)))
    sh = sh.filter(ImageFilter.GaussianBlur(5 * s))
    out.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0), sh)
    out.paste(glass.convert("RGBA"), (m, m), shape)
    d = ImageDraw.Draw(out)
    # 渐变描边：上沿亮、下沿一点反光，外加一圈琥珀色
    rim = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(rim).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=h * k // 2, outline=255, width=round(1.3 * s * k))
    rim = rim.resize((w, h), Image.LANCZOS)
    fade = np.asarray(rim, np.float32) * (0.25 + 0.6 * (1 - np.linspace(0, 1, h))[:, None])
    out.paste(Image.new("RGBA", (w, h), (255, 255, 255, 255)), (m, m), Image.fromarray(fade.astype(np.uint8), "L"))
    d.text((W / 2, H / 2), text, font=font, fill=AMBER + (255,), anchor="mm")
    return out


class Marker:
    def __init__(self):
        self.hwnd = None
        self.q: queue.Queue = queue.Queue()
        self._proc = WNDPROC(self._wndproc)   # 留住引用，免得被回收
        self._ready = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="marker").start()
        self._ready.wait(3)

    def show(self, x: int, y: int, img: Image.Image):
        """(x, y) 为胶囊中心（屏幕坐标）。"""
        self.q.put(("show", x - img.width // 2, y - img.height // 2, img))
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP, 0, 0)

    def hide(self):
        self.q.put(("hide",))
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP, 0, 0)

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_APP:
            cmd = None
            while not self.q.empty():
                cmd = self.q.get_nowait()   # 只处理最新的一条
            if cmd and cmd[0] == "show":
                self._blit(*cmd[1:])
                user32.ShowWindow(hwnd, 8)  # SW_SHOWNA：显示但不抢焦点
            elif cmd:
                user32.ShowWindow(hwnd, 0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _blit(self, x: int, y: int, img: Image.Image):
        w, h = img.size
        r, g, b, a = img.convert("RGBa").split()        # 预乘 alpha
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()
        sdc = user32.GetDC(None)
        mdc = gdi32.CreateCompatibleDC(sdc)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(mdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(mdc, bmp)
        pt, size, src = wt.POINT(x, y), wt.SIZE(w, h), wt.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        user32.UpdateLayeredWindow(self.hwnd, sdc, ctypes.byref(pt), ctypes.byref(size), mdc, ctypes.byref(src), 0,
                                   ctypes.byref(blend), 2)
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)

    def _loop(self):
        inst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(0, self._proc, 0, 0, inst, None, None, None, None, "DungeonMarkerGlass")
        user32.RegisterClassW(ctypes.byref(wc))
        ex = 0x80000 | 0x20 | 0x8 | 0x80 | 0x08000000   # LAYERED | TRANSPARENT | TOPMOST | TOOLWINDOW | NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, "DungeonMarkerGlass", "推荐标记", 0x80000000, 0, 0, 1, 1,
                                           None, None, inst, None)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
