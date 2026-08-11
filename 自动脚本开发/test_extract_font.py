"""从 HP 区域提取真实数字字模"""
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

keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
matches = [(h, t, l, tp, r, b) for h, t, l, tp, r, b in enum_windows() if keyword in t]
if not matches: print("未找到"); exit()
hwnd = matches[0][0]
buf = ctypes.create_unicode_buffer(256); ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
win_title = buf.value.strip()

settings = json.loads((PROJECT_DIR / "maps" / "system_setting.json").read_text(encoding="utf-8"))
entry = settings.get(win_title, {})
hp_rect = entry.get("hp_rect")
mp_rect = entry.get("mp_rect")

frame = capture_window(hwnd)
fh, fw = frame.shape[:2]

# 收集所有 HP+MP 区域的字符
all_chars = {}
for label, rect in [("HP", hp_rect), ("MP", mp_rect)]:
    x1, y1, x2, y2 = rect
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in sorted(contours, key=lambda c: cv2.boundingRect(c)[0]):
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 2 or h < 4: continue
        char_img = binary[y:y+h, x:x+w]

        # 归一化到 7 高
        if h != 7:
            scale_y = 7 / h
            char_img = cv2.resize(char_img, None, fx=scale_y, fy=scale_y,
                                    interpolation=cv2.INTER_AREA) > 128
        char_img = char_img.astype(bool)

        # 限制为 5 宽（居中）
        cw = char_img.shape[1]
        if cw > 5:
            char_img = char_img[:, :5]
        elif cw < 5:
            char_img = np.pad(char_img, ((0,0), (0,5-cw)), constant_values=False)

        # 转为字符串
        pattern = tuple(''.join('#' if char_img[r,c] else '.' for c in range(5))
                       for r in range(7))
        if pattern not in all_chars:
            all_chars[pattern] = []
        all_chars[pattern].append(label)

# 打印
print(f"HP区域: {hp_rect}  MP区域: {mp_rect}")
print(f"\n从游戏提取了 {len(all_chars)} 个不同字模:\n")
for pattern, labels in all_chars.items():
    for row in pattern:
        print(f"  '{row}',")
    print(f"  → 出现在: {labels}")
    print()