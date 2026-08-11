"""Windows API 工具函数 — 枚举窗口、激活窗口、查找窗口。"""

import ctypes
import ctypes.wintypes

import numpy as np

# ---- PrintWindow 截取（客户区坐标，与自动脚本一致） ----

class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]

# ---- DPI awareness 上下文切换 ----
_DPI_AWARENESS_CONTEXT_UNAWARE = ctypes.c_void_p(-1)
_user32_dpi = ctypes.windll.user32

class _dpi_unaware:
    """临时把当前线程 DPI awareness 切到 UNAWARE。"""
    def __enter__(self):
        try:
            self._prev = _user32_dpi.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_UNAWARE)
        except Exception:
            self._prev = None
        return self
    def __exit__(self, *a):
        try:
            if self._prev:
                _user32_dpi.SetThreadDpiAwarenessContext(self._prev)
        except Exception:
            pass


def capture_client(hwnd: int) -> np.ndarray | None:
    """PrintWindow(PW_CLIENTONLY) 截取窗口客户区，返回 BGR numpy 数组。

    DPI-aware 线程下 GetClientRect 返回物理像素但 PrintWindow 按逻辑像素绘制，
    导致只有左上角有内容。用 _dpi_unaware 上下文确保 bitmap/Paint 对齐。
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
            bmi = _BIH()
            bmi.biSize = ctypes.sizeof(_BIH)
            bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = w, -h, 1, 32
            bmi.biSizeImage = 4 * w * h
            ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi, ctypes.byref(bmi), 0)
            img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
            ctypes.windll.gdi32.SelectObject(memdc, old)
            ctypes.windll.gdi32.DeleteObject(bmp)
            ctypes.windll.gdi32.DeleteDC(memdc)
        finally:
            ctypes.windll.user32.ReleaseDC(hwnd, hdc)
    return img


def enum_visible_windows(min_w: int = 80, min_h: int = 20) -> list:
    """枚举所有可见窗口，按面积降序排列。

    Returns:
        list[tuple]: [(hwnd, title, left, top, right, bottom, pid), ...]
    """
    found = []

    def cb(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.right - r.left) < min_w or (r.bottom - r.top) < min_h:
            return True
        title = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
        if not title.value.strip():
            return True
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title.value, r.left, r.top, r.right, r.bottom, pid.value))
        return True

    WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WEP(cb), 0)
    found.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    return found


def force_foreground(hwnd) -> None:
    """强制将指定窗口置于前台（topmost + foreground）。"""
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.c_ulong())
    att = False
    if cur != tgt:
        ctypes.windll.user32.AttachThreadInput(cur, tgt, True)
        att = True
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)
    finally:
        if att:
            ctypes.windll.user32.AttachThreadInput(cur, tgt, False)


def find_windows_by_title(title: str) -> list:
    """按标题模糊匹配查找窗口，返回所有匹配列表。

    Returns:
        list: [(hwnd, title, left, top, right, bottom, pid), ...]  按面积降序
    """
    if not title:
        return []
    title_lower = title.lower()
    matches = []
    for hwnd, wt, l, t, r, b, pid in enum_visible_windows(min_w=80, min_h=20):
        if title_lower in wt.lower():
            matches.append((hwnd, wt, l, t, r, b, pid))
    matches.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    return matches


def find_window_by_title(title: str):
    """按标题模糊匹配查找窗口。返回面积最大的匹配。

    Returns:
        (hwnd, title, left, top, right, bottom) or None
    """
    matches = find_windows_by_title(title)
    if not matches:
        return None
    hwnd, wt, l, t, r, b, _ = matches[0]
    return (hwnd, wt, l, t, r, b)
