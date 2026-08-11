"""HP/MP 识别调试 — 保存原始裁剪 + 预处理各阶段图像"""

import ctypes, ctypes.wintypes, json, sys
from pathlib import Path
import cv2, numpy as np

PROJECT_DIR = Path(__file__).resolve().parent

class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]

def capture_window(hwnd):
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0: return None
    hdc = ctypes.windll.user32.GetDC(hwnd)
    if not hdc: return None
    try:
        memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = ctypes.windll.gdi32.SelectObject(memdc, bmp)
        ctypes.windll.user32.PrintWindow(hwnd, memdc, 1)
        bi = ctypes.create_string_buffer(4 * w * h)
        bmi = _BMIH(); bmi.biSize = ctypes.sizeof(_BMIH)
        bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = w, -h, 1, 32
        bmi.biSizeImage = 4 * w * h
        ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi, ctypes.byref(bmi), 0)
        img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
        ctypes.windll.gdi32.SelectObject(memdc, old)
        ctypes.windll.gdi32.DeleteObject(bmp); ctypes.windll.gdi32.DeleteDC(memdc)
    finally:
        ctypes.windll.user32.ReleaseDC(hwnd, hdc)
    return img

def enum_windows():
    found = []
    def cb(hwnd, _):
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 300 or h < 200: return True
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        t = buf.value.strip()
        if t: found.append((hwnd, t, r.left, r.top, r.right, r.bottom))
        return True
    WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WEP(cb), 0)
    found.sort(key=lambda x: (x[5]-x[3])*(x[4]-x[2]), reverse=True)
    return found

out = PROJECT_DIR / "test_output"
out.mkdir(exist_ok=True)

keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
matches = [(h, t, l, tp, r, b) for h, t, l, tp, r, b in enum_windows() if keyword in t]
if not matches: print("未找到窗口"); exit()
hwnd, title, gx, gy, gr, gb = matches[0]
buf = ctypes.create_unicode_buffer(256)
ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
win_title = buf.value.strip()

ss_path = PROJECT_DIR / "maps" / "system_setting.json"
settings = json.loads(ss_path.read_text(encoding="utf-8"))
entry = settings.get(win_title, {})
hp_rect = entry.get("hp_rect")
mp_rect = entry.get("mp_rect")
print(f"HP区域: {hp_rect}  MP区域: {mp_rect}")

frame = capture_window(hwnd)
fh, fw = frame.shape[:2]
print(f"截图: {fw}x{fh}")

for label, rect, color in [("hp", hp_rect, (0,0,255)), ("mp", mp_rect, (255,0,0))]:
    x1, y1, x2, y2 = rect
    crop = frame[y1:y2, x1:x2]
    # 保存原始裁剪
    _, buf = cv2.imencode(".png", crop)
    (out / f"{label}_raw.png").write_bytes(buf.tobytes())

    # 放大 4 倍
    big = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    _, buf = cv2.imencode(".png", big)
    (out / f"{label}_x4.png").write_bytes(buf.tobytes())

    # 预处理
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    _, buf = cv2.imencode(".png", thresh)
    (out / f"{label}_thresh_otsu.png").write_bytes(buf.tobytes())

    # 自适应二值化
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 11, 2)
    _, buf = cv2.imencode(".png", cv2.resize(adaptive, None, fx=4, fy=4,
                                              interpolation=cv2.INTER_NEAREST))
    (out / f"{label}_adaptive_x4.png").write_bytes(buf.tobytes())

    # 像素值统计
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    print(f"\n{label.upper()} 原始: {crop.shape[1]}x{crop.shape[0]}")
    print(f"  BGR mean: [{gray.mean():.0f}]")
    # 检查是否有白色文字
    white_mask = gray > 200
    print(f"  白色像素(>200): {white_mask.sum()} / {gray.size}")
    black_mask = gray < 30
    print(f"  黑色像素(<30): {black_mask.sum()} / {gray.size}")

    # 尝试简单二值化
    for thr in [100, 120, 150, 180, 200]:
        _, b = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        wp = (b > 0).sum()
        if 20 < wp < 500:
            _, buf = cv2.imencode(".png", cv2.resize(b, None, fx=4, fy=4,
                                                      interpolation=cv2.INTER_NEAREST))
            (out / f"{label}_thr{thr}_x4_{wp}.png").write_bytes(buf.tobytes())
            print(f"  thr={thr}: 白像素={wp}")

print(f"\n调试图片: test_output/hp_*.png  mp_*.png")
