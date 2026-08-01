"""utils.py — Windows API 窗口操作"""

import ctypes
import ctypes.wintypes
from datetime import datetime
from pathlib import Path

import config
from config import TARGET_W, TARGET_H, IMAGE_FORMAT


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


def capture_and_save(sct, hwnd: int, output_dir: Path, map_name: str = "") -> str | None:
    """截取窗口区域，缩放到 720p 保存。返回文件名。map_name 会作为文件名前缀。"""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    if right <= left or bottom <= top:
        return None
    region = {"left": max(0, left), "top": max(0, top),
              "width": right - left, "height": bottom - top}
    img_raw = sct.grab(region)
    img = config.Image.frombytes("RGB", img_raw.size, img_raw.bgra, "raw", "BGRX")
    img = img.resize((TARGET_W, TARGET_H), config.Image.LANCZOS)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    prefix = f"{map_name}_" if map_name else ""
    filename = f"{prefix}shot_{timestamp}.{IMAGE_FORMAT.lower()}"
    filepath = output_dir / filename
    img.save(str(filepath), IMAGE_FORMAT)
    return filename
