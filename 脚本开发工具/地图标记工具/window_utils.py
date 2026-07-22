"""Windows API 工具函数 — 枚举窗口、激活窗口、查找窗口。"""

import ctypes
import ctypes.wintypes


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


def find_window_by_title(title: str):
    """按标题模糊匹配查找窗口。返回面积最大的匹配。

    Returns:
        (hwnd, title, left, top, right, bottom) or None
    """
    if not title:
        return None
    title_lower = title.lower()
    matches = []
    for hwnd, wt, l, t, r, b, pid in enum_visible_windows(min_w=80, min_h=20):
        if title_lower in wt.lower():
            matches.append((hwnd, wt, l, t, r, b, pid))
    if not matches:
        return None
    matches.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    hwnd, wt, l, t, r, b, _ = matches[0]
    return (hwnd, wt, l, t, r, b)
