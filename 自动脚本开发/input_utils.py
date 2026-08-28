"""input_utils.py — 按键控制 + Windows 窗口/截图 API"""

import ctypes
import ctypes.wintypes
import time

import mss
import numpy as np

from config import KEY_MAP

# ---- PrintWindow 截取结构体 ----

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]

# ---- DPI awareness 上下文切换 ----
_DPI_AWARENESS_CONTEXT_UNAWARE = ctypes.c_void_p(-1)

class _dpi_unaware:
    """临时把当前线程 DPI awareness 切到 UNAWARE。"""
    def __enter__(self):
        try:
            self._prev = ctypes.windll.user32.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_UNAWARE)
        except Exception:
            self._prev = None
        return self
    def __exit__(self, *a):
        try:
            if self._prev:
                ctypes.windll.user32.SetThreadDpiAwarenessContext(self._prev)
        except Exception:
            pass


def _capture_window(hwnd: int) -> np.ndarray | None:
    """用 PrintWindow 直接截取窗口客户区（不被其他窗口遮挡）。

    DPI-aware 线程下 GetClientRect 返回物理像素但 PrintWindow 按逻辑像素绘制，
    用 _dpi_unaware 上下文确保 bitmap 和 PrintWindow 都按 1:1 逻辑像素对齐。
    """
    with _dpi_unaware():
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(r))
        w, h = r.right, r.bottom
        if w <= 0 or h <= 0:
            return None
        hdc = ctypes.windll.user32.GetDC(hwnd)
        if not hdc:
            return None
        try:
            memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
            bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = ctypes.windll.gdi32.SelectObject(memdc, bmp)
            if not ctypes.windll.user32.PrintWindow(hwnd, memdc, 1):
                ctypes.windll.user32.PrintWindow(hwnd, memdc, 0)
            bi = ctypes.create_string_buffer(4 * w * h)
            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth, bmi.biHeight = w, -h
            bmi.biPlanes, bmi.biBitCount = 1, 32
            bmi.biSizeImage = 4 * w * h
            ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi, ctypes.byref(bmi), 0)
            img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
            ctypes.windll.gdi32.SelectObject(memdc, old)
            ctypes.windll.gdi32.DeleteObject(bmp)
            ctypes.windll.gdi32.DeleteDC(memdc)
        finally:
            ctypes.windll.user32.ReleaseDC(hwnd, hdc)
    return img

# ============================================================
# 按键控制
# ============================================================

class KeySender:
    def __init__(self) -> None:
        self._held: set[str] = set()
        self._log_cb: callable | None = None

    def set_log_callback(self, cb: callable) -> None:
        """设置按键日志回调 cb(key_name, action)"""
        self._log_cb = cb

    def _log(self, key: str, action: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(key, action)
            except Exception:
                pass

    def _kb(self, key: str, down: bool) -> None:
        vk, sc = KEY_MAP[key]
        # 方向键/Alt/Ctrl 需要 EXTENDEDKEY 标志，字母数字不需要
        extended = key in ('l', 'r', 'u', 'd', 'j', 'a')
        flags = 0x0001 if (extended and down) else 0
        if not down:
            flags |= 0x0002  # KEYEVENTF_KEYUP
        ctypes.windll.user32.keybd_event(vk, sc, flags, 0)

    def press(self, key: str) -> None:
        if key not in self._held:
            self._log(key, "press")
            self._kb(key, True)
            self._held.add(key)

    def release(self, key: str) -> None:
        if key in self._held:
            self._log(key, "release")
            self._kb(key, False)
            self._held.discard(key)

    def tap(self, key: str, duration: float = 0.10) -> None:
        self._log(key, "tap")
        self.press(key)
        time.sleep(duration)
        self.release(key)

    def hold_only(self, keys: tuple[str, ...]) -> None:
        for k in list(self._held):
            if k not in keys:
                self.release(k)
        for k in keys:
            self.press(k)

    def press_extra(self, key: str) -> None:
        """仅按下指定键，不释放其他已按住键。"""
        self.press(key)

    def force_release_all(self) -> None:
        for k in KEY_MAP:
            vk, sc = KEY_MAP[k]
            ctypes.windll.user32.keybd_event(vk, sc, 0x0003, 0)
        self._held.clear()

    def release_all(self) -> None:
        for k in list(self._held):
            self.release(k)


# ============================================================
# Windows API
# ============================================================

def find_window_by_title(title: str):
    found = []

    def cb(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.right - r.left) < 200 or (r.bottom - r.top) < 200:
            return True
        tbuf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, tbuf, 256)
        if not tbuf.value.strip():
            return True
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, tbuf.value, r.left, r.top, r.right, r.bottom, pid.value))
        return True

    WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WEP(cb), 0)
    found.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    title_lower = title.lower()
    matches = [f for f in found if title_lower in f[1].lower()]
    return (matches[0][0], matches[0][1], matches[0][2], matches[0][3],
            matches[0][4], matches[0][5]) if matches else None


def force_foreground(hwnd: int) -> None:
    """强制将窗口调到最前（多重手段，Windows 10+ SetForegroundWindow 常被拒绝）。

    依次尝试: 还原最小化 → BringWindowToTop/SetActiveWindow →
    SetForegroundWindow(AttachThreadInput 绕过前台限制) → 复查补拉。
    """
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE，先还原最小化
    # 1) BringWindowToTop + SetActiveWindow
    try:
        ctypes.windll.user32.BringWindowToTop(hwnd)
        ctypes.windll.user32.SetActiveWindow(hwnd)
    except Exception:
        pass
    # 2) SetForegroundWindow：AttachThreadInput 绕过前台限制
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.c_ulong())
    att = False
    if cur != tgt:
        ctypes.windll.user32.AttachThreadInput(cur, tgt, True)
        att = True
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    finally:
        if att:
            ctypes.windll.user32.AttachThreadInput(cur, tgt, False)
    # 3) 复查：仍不是前台则再补一次（部分游戏窗口需要）
    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        try:
            ctypes.windll.user32.BringWindowToTop(hwnd)
        except Exception:
            pass


def capture_frame(hwnd: int) -> np.ndarray | None:
    """截取游戏窗口全帧（PrintWindow，不被遮挡）。"""
    return _capture_window(hwnd)


def capture_minimap(hwnd: int, mm_region: tuple[int, ...]) -> np.ndarray | None:
    """截取小地图区域（从 PrintWindow 全帧中切片）。"""
    frame = _capture_window(hwnd)
    if frame is None:
        return None
    ml, mt, mr, mb = mm_region
    fh, fw = frame.shape[:2]
    if mr > fw or mb > fh or ml < 0 or mt < 0 or mr <= ml or mb <= mt:
        return None
    return frame[mt:mb, ml:mr].copy()


def enum_visible_windows() -> list[tuple[int, str]]:
    """枚举所有可见窗口，返回 [(hwnd, title), ...] 按标题排序。"""
    results: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum_proc(hwnd: int, _lparam: int) -> bool:
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title.strip():
                    results.append((hwnd, title))
        return True

    ctypes.windll.user32.EnumWindows(_enum_proc, 0)
    results.sort(key=lambda x: x[1].lower())
    return results
