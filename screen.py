"""看和动：截游戏窗口、Windows 自带中文 OCR、模拟鼠标键盘（SendInput）。

坐标一律用「相对游戏画面的比例」(0~1)，换分辨率也能用。
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass

import mss
import numpy as np
from PIL import Image

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

GAME_TITLE = "DungeonBrawl"


# ---------------------------------------------------------------- 窗口

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_game() -> int | None:
    hwnd = user32.FindWindowW(None, GAME_TITLE)
    return hwnd or None


def game_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    r = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = r.right - r.left, r.bottom - r.top
    return (pt.x, pt.y, w, h) if w > 200 and h > 200 else None


def game_is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def point_is_game(frame: "Frame", hwnd: int, fx: float, fy: float) -> bool:
    """要点的位置上最上面的窗口是不是游戏（防止点到挡在上面的别的程序）。"""
    x, y, w, h = frame.rect
    hit = user32.WindowFromPoint(wt.POINT(int(x + fx * w), int(y + fy * h)))
    root = user32.GetAncestor(hit, 2) if hit else 0  # GA_ROOT
    return root == hwnd


# ---------------------------------------------------------------- 截图

class Frame:
    """一帧游戏画面，提供按比例裁剪。"""

    def __init__(self, img: Image.Image, rect: tuple[int, int, int, int]):
        self.img, self.rect = img, rect
        self.w, self.h = img.size

    def crop(self, x0: float, y0: float, x1: float, y1: float) -> Image.Image:
        return self.img.crop((int(x0 * self.w), int(y0 * self.h), int(x1 * self.w), int(y1 * self.h)))

    def region_std(self, x0, y0, x1, y1) -> float:
        """区域像素的离散程度：空格子很平，放了东西会变大。"""
        a = np.asarray(self.crop(x0, y0, x1, y1).convert("L"), dtype=np.float32)
        return float(a.std())


_sct = None
# 截图用自己的一份 DLL 对象并声明好句柄类型（64 位句柄按 int 传会溢出）
_u32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
_u32.GetDC.restype = wt.HDC
_u32.GetDC.argtypes = [wt.HWND]
_u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_u32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


def _print_window(hwnd: int, w: int, h: int) -> Image.Image | None:
    """直接从游戏窗口取画面（PW_RENDERFULLCONTENT），别的窗口挡在上面也不影响。"""
    hdc = _u32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not _u32.PrintWindow(hwnd, mdc, 3):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return None
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        _u32.ReleaseDC(hwnd, hdc)


def grab(hwnd: int) -> Frame | None:
    global _sct
    rect = game_rect(hwnd)
    if not rect:
        return None
    x, y, w, h = rect
    img = _print_window(hwnd, w, h)
    if img is not None and np.asarray(img.resize((64, 36))).mean() > 3:  # 全黑说明这台机器上取不到，退回截屏
        return Frame(img, rect)
    if _sct is None:
        _sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    shot = _sct.grab({"left": x, "top": y, "width": w, "height": h})
    return Frame(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), rect)


# ---------------------------------------------------------------- OCR（Windows 自带，简体中文）

@dataclass
class TextLine:
    text: str
    x: float  # 中心点，相对整帧的比例
    y: float


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        _engine = OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
        if _engine is None:
            raise RuntimeError("Windows 没装简体中文 OCR：设置 → 时间和语言 → 语言 → 添加中文(简体)")
    return _engine


async def _ocr_async(img: Image.Image):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    rgba = img.convert("RGBA")
    b, g, r, a = rgba.split()[2], rgba.split()[1], rgba.split()[0], rgba.split()[3]
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(bgra)
    buf = writer.detach_buffer()
    bmp = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, rgba.width, rgba.height)
    return await _get_engine().recognize_async(bmp)


def _binarize(img: Image.Image, thresh: int = 150) -> tuple[Image.Image, int]:
    """深色底浅色字 → 白底黑字并留白，小数字识别率高很多。"""
    from PIL import ImageOps
    g = img.convert("L").point(lambda v: 0 if v > thresh else 255)
    pad = max(img.height // 2, 12)
    return ImageOps.expand(g, border=pad, fill=255).convert("RGB"), pad


def ocr(frame: Frame, box=(0, 0, 1, 1), scale: float = 1.0, binarize: bool | int = False) -> list[TextLine]:
    """识别区域里的文字，返回每行文字和它在整帧里的位置。"""
    x0, y0, x1, y1 = box
    img = frame.crop(x0, y0, x1, y1)
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    pad = 0
    w0, h0 = img.size
    if binarize:
        img, pad = _binarize(img, binarize if binarize is not True else 150)
    result = asyncio.run(_ocr_async(img))
    out = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        text = "".join(w.text for w in words).replace(" ", "")
        rx = [w.bounding_rect for w in words]
        cx = ((min(r.x for r in rx) + max(r.x + r.width for r in rx)) / 2 - pad) / w0
        cy = ((min(r.y for r in rx) + max(r.y + r.height for r in rx)) / 2 - pad) / h0
        out.append(TextLine(text, x0 + cx * (x1 - x0), y0 + cy * (y1 - y0)))
    return out


def ocr_text(frame: Frame, box, scale: float = 1.0, binarize: bool | int = False) -> str:
    return "".join(t.text for t in ocr(frame, box, scale, binarize))


def ocr_words(frame: Frame, box, scale: float = 2.0, binarize: bool | int = False) -> list[TextLine]:
    """按词返回（单个数字和别的数字连在一行时，OCR 才认得出来）。"""
    x0, y0, x1, y1 = box
    img = frame.crop(x0, y0, x1, y1)
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    w0, h0 = img.size
    pad = 0
    if binarize:
        img, pad = _binarize(img, binarize if binarize is not True else 150)
    out = []
    for line in asyncio.run(_ocr_async(img)).lines:
        for w in line.words:
            r = w.bounding_rect
            out.append(TextLine(w.text, x0 + ((r.x + r.width / 2 - pad) / w0) * (x1 - x0),
                                y0 + ((r.y + r.height / 2 - pad) / h0) * (y1 - y0)))
    return out


def ocr_best(frame: Frame, box, check, tries=((1.5, 190), (1.5, False), (1.8, 190), (1.2, 190), (1.5, 220),
                                              (2.0, False), (2.5, 150))) -> tuple[str, str]:
    """换几套放大倍数 / 黑白阈值轮流识别，返回第一个让 check(文字) 有结果的（文字, 结果）；都不行返回第一个非空文字。
    游戏里白字描边、背景花，单一参数经常读不出，实测阈值 190 左右最稳。"""
    first = ""
    for scale, b in tries:
        text = ocr_text(frame, box, scale, b)
        if not text:
            continue
        hit = check(text)
        if hit:
            return text, hit
        first = first or text
    return first, ""


def ocr_number(frame: Frame, box, scale: float = 3.0) -> int | None:
    """读一个数字（金币、骷髅币、价格），读不出返回 None。"""
    for b in (True, False):
        txt = ocr_text(frame, box, scale, binarize=b).replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
        digits = "".join(ch for ch in txt if ch.isdigit())
        if digits:
            return int(digits)
    return None


# ---------------------------------------------------------------- 输入（SendInput）

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _send(*inputs: INPUT):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, dx=0, dy=0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return i


def move_to(frame: Frame, fx: float, fy: float):
    x, y, w, h = frame.rect
    px, py = x + fx * w, y + fy * h
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # 虚拟屏幕左上
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    ax = int((px - vx) * 65535 / max(vw - 1, 1))
    ay = int((py - vy) * 65535 / max(vh - 1, 1))
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | 0x4000, ax, ay))  # 0x4000 = VIRTUALDESK


def click(frame: Frame, fx: float, fy: float, button: str = "left", double: bool = False):
    move_to(frame, fx, fy)
    time.sleep(0.05)
    down, up = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button == "left" else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    for _ in range(2 if double else 1):
        _send(_mouse(down))
        time.sleep(0.04)
        _send(_mouse(up))
        time.sleep(0.06)


def scroll(frame: Frame, fx: float, fy: float, ticks: int):
    """正数向上，负数向下。"""
    move_to(frame, fx, fy)
    time.sleep(0.05)
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(0, 0, ctypes.c_ulong(120 * ticks & 0xFFFFFFFF).value, 0x0800, 0, 0)
    _send(i)
    time.sleep(0.15)


VK = {"tab": 0x09, "esc": 0x1B, "enter": 0x0D, "space": 0x20,
      **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"}, **{str(i): 0x30 + i for i in range(10)}}


def _key_input(name: str, up: bool) -> INPUT:
    vk = VK[name.lower()]
    scan = user32.MapVirtualKeyW(vk, 0)
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(vk, scan, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
    return i


def key_down(name: str):
    _send(_key_input(name, False))


def key_up(name: str):
    _send(_key_input(name, True))


def key(name: str):
    key_down(name)
    time.sleep(0.05)
    key_up(name)
