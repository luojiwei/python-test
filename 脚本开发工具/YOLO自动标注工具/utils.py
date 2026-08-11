"""utils.py — Windows API 窗口操作 + PrintWindow 截图"""

import ctypes
import ctypes.wintypes
import numpy as np
from datetime import datetime
from pathlib import Path

import config
from config import TARGET_W, TARGET_H, IMAGE_FORMAT


# ---- PrintWindow 内部结构 ----
class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def capture_client(hwnd: int) -> np.ndarray | None:
    """PrintWindow(PW_CLIENTONLY) 截取窗口客户区，返回 BGR numpy 数组。

    关键：DPI-aware 线程下 GetClientRect 返回物理像素 (2732×1536)，
    但 PrintWindow 实际只按逻辑像素 (1366×768) 绘制，导致只有左上角
    有内容。修复：捕获前把线程切到 DPI_UNAWARE，让 GetClientRect
    返回逻辑尺寸，bitmap 和 PrintWindow 都按 1:1 像素对齐。
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


def enum_visible_windows(min_width: int = 100, min_height: int = 100) -> list:
    """枚举所有可见窗口，返回 [(hwnd, title, left, top, right, bottom, pid), ...]"""
    found: list = []
    def callback(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < min_width or h < min_height:
            return True
        title = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
        if not title.value.strip():
            return True
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title.value, rect.left, rect.top, rect.right, rect.bottom, pid.value))
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), 0)
    found.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    return found


def find_window_by_title(title: str) -> list:
    """按窗口标题模糊匹配。"""
    title_lower = title.lower()
    return [(hwnd, wt, l, t, r, b, pid) for hwnd, wt, l, t, r, b, pid
            in enum_visible_windows() if title_lower in wt.lower()]


def force_foreground(hwnd: int) -> None:
    """强制将窗口提到最前。"""
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)
    cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    target_tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.c_ulong())
    attached = cur_tid != target_tid
    if attached:
        ctypes.windll.user32.AttachThreadInput(cur_tid, target_tid, True)
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur_tid, target_tid, False)


def capture_and_save(hwnd: int, output_dir: Path, map_name: str = "") -> str | None:
    """用 PrintWindow 截取窗口，缩放到 TARGET_W×TARGET_H 保存。返回文件名。"""
    frame = capture_client(hwnd)
    if frame is None:
        return None
    img_pil = config.Image.fromarray(frame[:, :, ::-1])  # BGR → RGB
    img_pil = img_pil.resize((TARGET_W, TARGET_H), config.Image.LANCZOS)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    prefix = f"{map_name}_" if map_name else ""
    filename = f"{prefix}shot_{timestamp}.{IMAGE_FORMAT.lower()}"
    filepath = output_dir / filename
    img_pil.save(str(filepath), IMAGE_FORMAT)
    return filename
