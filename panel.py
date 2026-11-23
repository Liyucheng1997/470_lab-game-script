"""液态玻璃面板：pywebview（WebView2）无边框置顶窗口，界面在 ui/。

桌面浮窗截不到身后的东西，所以玻璃的「身后」由我们自己给：游戏画面本来就在截（PrintWindow），
裁出面板背后那一块，缩小、模糊后塞给网页当背景，网页再叠墨黑薄色、高光边（见 ui/glass.css）。
"""
from __future__ import annotations

import base64
import ctypes
import io
import json
import threading
import time
from pathlib import Path

import webview
from PIL import ImageFilter

import screen as S

UI = Path(__file__).resolve().parent / "ui"
TITLE = "地下城军师"
user32 = ctypes.windll.user32


class Panel:
    def __init__(self, api: object):
        self.window = webview.create_window(
            TITLE, str(UI / "index.html"), js_api=api, width=320, height=520, x=1580, y=100,
            frameless=True, easy_drag=False, on_top=True, resizable=True, background_color="#16161a",
            text_select=False, focus=True)
        self.hwnd = 0
        self.ready = False
        self.visible = True
        self.dragged = False
        self._placing = False
        self._last_state = ""
        self._last_backdrop = 0.0
        self._lock = threading.Lock()
        self.window.events.moved += self._on_moved

    # ------------------------------------------------ 窗口
    def on_shown(self):
        for _ in range(50):
            self.hwnd = user32.FindWindowW(None, TITLE)
            if self.hwnd:
                break
            time.sleep(0.1)
        if self.hwnd:
            dwm = ctypes.windll.dwmapi
            for attr, val in ((33, 2), (20, 1)):    # Win11 圆角、深色标题栏
                v = ctypes.c_int(val)
                dwm.DwmSetWindowAttribute(self.hwnd, attr, ctypes.byref(v), 4)
        self.place()

    def _on_moved(self, *_):
        if not self._placing and self.ready:
            self.dragged = True                      # 用户自己拖过就不再自动跟着游戏走

    def place(self):
        """放在游戏画面右侧（卡片右边、背包上面），不挡三选一和背包。"""
        if self.dragged or not self.visible or (self.hwnd and not user32.IsWindow(self.hwnd)):
            return
        hwnd = S.find_game()
        rect = S.game_rect(hwnd) if hwnd else None
        if not rect:
            return
        x, y, w, h = rect
        scale = self.dpi_scale(hwnd)
        pw, ph = int(w * 0.165 / scale), int(h * 0.47 / scale)
        self._placing = True
        try:
            self.window.resize(pw, ph)
            self.window.move(int((x + w * 0.83) / scale), int((y + h * 0.09) / scale))
        finally:
            self._placing = False

    def dpi_scale(self, game_hwnd: int) -> float:
        """面板窗口的缩放；窗口正在关 / 读不到时，用游戏所在显示器的缩放。"""
        dpi = user32.GetDpiForWindow(self.hwnd) if self.hwnd and user32.IsWindow(self.hwnd) else 0
        if not dpi:
            try:
                mon = user32.MonitorFromWindow(ctypes.c_void_p(game_hwnd), 2)   # MONITOR_DEFAULTTONEAREST
                x, y = ctypes.c_uint(), ctypes.c_uint()
                if ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(mon), 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                    dpi = x.value
            except Exception:
                pass
        return (dpi or 96) / 96

    def rect(self) -> tuple[int, int, int, int] | None:
        if not self.hwnd:
            return None
        r = S.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def keep_on_top(self):
        if self.hwnd and self.visible:
            user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10)

    def toggle(self):
        self.visible = not self.visible
        if self.visible:
            self.window.show()
            self.keep_on_top()
            self.focus_game()
        else:
            self.window.hide()

    @staticmethod
    def focus_game():
        hwnd = S.find_game()
        if hwnd:
            user32.SetForegroundWindow(hwnd)

    # ------------------------------------------------ 推内容
    def push(self, state: dict):
        if not self.ready:
            return
        data = json.dumps(state, ensure_ascii=False, default=str)
        if data == self._last_state:
            return
        self._last_state = data
        with self._lock:
            try:
                self.window.evaluate_js(f"window.render({data})")
            except Exception as e:
                print("面板刷新失败：", e)

    def backdrop(self, frame: S.Frame | None, every: float = 0.8):
        """把面板身后那块游戏画面（模糊过）给网页当玻璃底。"""
        now = time.time()
        if not self.ready or not self.visible or frame is None or now - self._last_backdrop < every:
            return
        r = self.rect()
        if not r:
            return
        self._last_backdrop = now
        gx, gy, gw, gh = frame.rect
        x, y, w, h = r
        x0, y0 = (x - gx) / gw, (y - gy) / gh
        x1, y1 = (x + w - gx) / gw, (y + h - gy) / gh
        if x1 <= 0 or y1 <= 0 or x0 >= 1 or y0 >= 1:
            return
        img = frame.crop(max(x0, 0), max(y0, 0), min(x1, 1), min(y1, 1))
        img = img.resize((max(img.width // 5, 8), max(img.height // 5, 8))).filter(ImageFilter.GaussianBlur(2))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=70)
        url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        with self._lock:
            try:
                self.window.evaluate_js(f"window.setBackdrop('{url}')")
            except Exception:
                pass
