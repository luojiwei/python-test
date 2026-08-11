"""显示每个字符的实际像素特征"""
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
if not matches: print("未找到"); exit()
hwnd = matches[0][0]
buf = ctypes.create_unicode_buffer(256)
ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
win_title = buf.value.strip()

settings = json.loads((PROJECT_DIR / "maps" / "system_setting.json").read_text(encoding="utf-8"))
entry = settings.get(win_title, {})
hp_rect = entry.get("hp_rect")

frame = capture_window(hwnd)
x1, y1, x2, y2 = hp_rect
crop = frame[y1:y2, x1:x2]
gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
_, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
chars_by_x = []
for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    if w < 2 or h < 4: continue
    chars_by_x.append((x, y, w, h, binary[y:y+h, x:x+w]))
chars_by_x.sort()

print(f"HP区域: {hp_rect}, 裁剪: {crop.shape[1]}x{crop.shape[0]}")
print(f"字符数: {len(chars_by_x)}")
print(f"\n每个字符的像素特征:")

for i, (x, y, w, h, char_img) in enumerate(chars_by_x):
    # 黑色为字（OTSU: 黑字白底）
    black = char_img < 128
    total = black.sum()
    density = total / char_img.size

    # 分 3 段
    th = max(1, h // 3)
    top_d = black[:th, :].sum() / max(1, th * w)
    mid_d = black[th:2*th, :].sum() / max(1, th * w)
    bot_d = black[2*th:, :].sum() / max(1, (h-2*th) * w)

    # 打印像素矩阵
    rows = []
    for r in range(h):
        row = ""
        for c in range(w):
            row += "#" if black[r, c] else "."
        rows.append(row)
    print(f"\n字符 {i}: x={x} y={y} {w}x{h} density={density:.2f} top={top_d:.2f} mid={mid_d:.2f} bot={bot_d:.2f}")
    for row in rows:
        print(f"  {row}")