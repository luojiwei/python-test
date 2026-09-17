"""临时诊断：在开场缓冲 / 阶段运行中分别抓一张窗口图（PrintWindow，不遮挡其它窗口）。"""
import ctypes
import ctypes.wintypes
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

APP = Path(r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\循环按键脚本")
PYW = r"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\pythonw.exe"

u = ctypes.windll.user32
g = ctypes.windll.gdi32
u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_void_p]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
    ]


def find_window(pid):
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits = []

    def cb(hwnd, _):
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip():
                hits.append((hwnd, buf.value))
        return True

    u.EnumWindows(callback_type(cb), 0)
    for hwnd, title in hits:
        if title == "循环按键脚本":
            return hwnd, title
    return hits[0] if hits else None


def capture(hwnd, path):
    rect = ctypes.wintypes.RECT()
    u.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    hdc = u.GetDC(hwnd)
    mem = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mem, bmp)
    ok = u.PrintWindow(hwnd, mem, 1)          # PW_CLIENTONLY
    buf = ctypes.create_string_buffer(4 * w * h)
    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = w, -h
    info.biPlanes, info.biBitCount, info.biSizeImage = 1, 32, 4 * w * h
    g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3][:, :, ::-1]
    Image.fromarray(arr.copy()).save(str(path))
    g.DeleteObject(bmp)
    g.DeleteDC(mem)
    u.ReleaseDC(hwnd, hdc)
    return ok, (w, h)


def main():
    proc = subprocess.Popen([PYW, str(APP / "main.py")], cwd=str(APP))
    print("已启动 PID:", proc.pid)
    time.sleep(3.5)
    hwnd, title = find_window(proc.pid)
    print("窗口:", title)
    u.ShowWindow(hwnd, 9)
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)
    time.sleep(0.8)

    u.keybd_event(0x7B, 0, 0, 0)
    time.sleep(0.08)
    u.keybd_event(0x7B, 0, 2, 0)
    print("已注入 F12")

    time.sleep(0.6)
    print("缓冲期抓图:", capture(hwnd, APP / "_ui_countdown.png"))
    time.sleep(4.0)
    print("运行期抓图:", capture(hwnd, APP / "_ui_running.png"))

    k = ctypes.windll.kernel32
    h = k.OpenProcess(1, False, proc.pid)
    k.TerminateProcess(h, 0)
    k.CloseHandle(h)
    print("已结束 PID:", proc.pid)


main()
