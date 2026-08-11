"""查看模板到底长什么样"""
import ctypes, ctypes.wintypes, cv2, json, numpy as np
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
    t = buf.value.strip()
    if "冒险岛" in t: found.append((hwnd, t))
    return True
WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
ctypes.windll.user32.EnumWindows(WEP(cb), 0)
hwnd, title = found[0]

r = ctypes.wintypes.RECT()
ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
w, h = r.right - r.left, r.bottom - r.top
hdc = ctypes.windll.user32.GetDC(hwnd)
memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
old = ctypes.windll.gdi32.SelectObject(memdc, bmp)
ctypes.windll.user32.PrintWindow(hwnd, memdc, 1)
bi = ctypes.create_string_buffer(4 * w * h)
bmi = _BIH(); bmi.biSize = ctypes.sizeof(_BIH)
bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = w, -h, 1, 32
bmi.biSizeImage = 4 * w * h
ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi, ctypes.byref(bmi), 0)
img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
ctypes.windll.gdi32.SelectObject(memdc, old)
ctypes.windll.gdi32.DeleteObject(bmp); ctypes.windll.gdi32.DeleteDC(memdc)
ctypes.windll.user32.ReleaseDC(hwnd, hdc)

ss_path = Path(__file__).resolve().parent / "maps" / "system_setting.json"
ss = json.loads(ss_path.read_text(encoding="utf-8"))
e = ss.get(title, {})
tx1, ty1, tx2, ty2 = e["template_rect"]

# 保存模板图片（放大4倍看得清）
template = img[ty1:ty2, tx1:tx2]
big = cv2.resize(template, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)

# 截模板周围 200x200 区域看上下文
cx2 = min(tx1 + 100, w); cx1 = max(tx2 - 100, 0)
cy2 = min(ty1 + 100, h); cy1 = max(ty2 - 100, 0)
context = img[cy1:cy2, cx1:cx2]

pp = Path(__file__).resolve().parent / "test_output"
pp.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", big)
(pp / "template_x4.png").write_bytes(buf.tobytes())
_, buf = cv2.imencode(".png", context)
(pp / "template_context.png").write_bytes(buf.tobytes())

print(f"模板区域: ({tx1},{ty1})-({tx2},{ty2}) = {tx2-tx1}x{ty2-ty1}px")
print(f"模板像素均值 BGR: ({template[:,:,0].mean():.0f},{template[:,:,1].mean():.0f},{template[:,:,2].mean():.0f})")
print(f"已保存: template_x4.png  template_context.png")
