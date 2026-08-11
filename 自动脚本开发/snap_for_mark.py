"""截游戏窗口，让用户标记角色名位置"""
import ctypes, ctypes.wintypes, cv2, numpy as np
from pathlib import Path

class _BIH(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]

found = []
def cb(hwnd, _):
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w < 300 or h < 200: return True
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    if "冒险岛" in buf.value: found.append((hwnd, w, h))
    return True
WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
ctypes.windll.user32.EnumWindows(WEP(cb), 0)
if not found: print("游戏未运行"); exit()
hwnd, fw, fh = found[0]

hdc = ctypes.windll.user32.GetDC(hwnd)
memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, fw, fh)
old = ctypes.windll.gdi32.SelectObject(memdc, bmp)
ctypes.windll.user32.PrintWindow(hwnd, memdc, 1)
bi = ctypes.create_string_buffer(4 * fw * fh)
bmi = _BIH(); bmi.biSize = ctypes.sizeof(_BIH)
bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = fw, -fh, 1, 32
bmi.biSizeImage = 4 * fw * fh
ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, fh, bi, ctypes.byref(bmi), 0)
img = np.frombuffer(bi, dtype=np.uint8).reshape(fh, fw, 4)[:, :, :3].copy()
ctypes.windll.gdi32.SelectObject(memdc, old)
ctypes.windll.gdi32.DeleteObject(bmp); ctypes.windll.gdi32.DeleteDC(memdc)
ctypes.windll.user32.ReleaseDC(hwnd, hdc)

pp = Path(__file__).resolve().parent / "test_output" / "snap_for_mark.png"
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", img)
pp.write_bytes(buf.tobytes())
print(f"{fw}x{fh}")
print(f"已保存: {pp}")
