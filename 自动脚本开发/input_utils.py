"""input_utils.py — 按键控制 + Windows 窗口/截图 API"""

import ctypes
import ctypes.wintypes
import time

import mss
import numpy as np

from config import KEY_MAP

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
        ext = 0x0001  # KEYEVENTF_EXTENDEDKEY（非扩展键无害，扩展键必须）
        ctypes.windll.user32.keybd_event(vk, sc, ext if down else ext | 0x0002, 0)

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
    finally:
        if att:
            ctypes.windll.user32.AttachThreadInput(cur, tgt, False)


def capture_frame(hwnd: int) -> np.ndarray | None:
    try:
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
        if gr <= gl or gb <= gt:
            return None
        region = {"left": max(0, gl), "top": max(0, gt),
                  "width": gr - gl, "height": gb - gt}
        with mss.mss() as sct:
            img_raw = sct.grab(region)
        return np.array(img_raw)[:, :, :3]
    except Exception:
        return None


def capture_minimap(hwnd: int, mm_region: tuple[int, ...]) -> np.ndarray | None:
    try:
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        gl, gt = r.left, r.top
        ml, mt, mr, mb = mm_region
        mw, mh = mr - ml, mb - mt
        if mw <= 0 or mh <= 0:
            return None
        region = {"left": gl + ml, "top": gt + mt, "width": mw, "height": mh}
        with mss.mss() as sct:
            return np.array(sct.grab(region))[:, :, :3]
    except Exception:
        return None


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
