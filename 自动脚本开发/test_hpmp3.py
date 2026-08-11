"""详细调试 — 显示每个字符的识别结果"""
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

from auto_potion import _init_templates, _DIGIT_TEMPLATES, _recognize_char

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
mp_rect = entry.get("mp_rect")

frame = capture_window(hwnd)
_init_templates()

print(f"\n模板尺寸: " + ", ".join(f"{c}:{t.shape[1]}x{t.shape[0]}" for c, t in _DIGIT_TEMPLATES.items()))

for label, rect in [("HP", hp_rect), ("MP", mp_rect)]:
    x1, y1, x2, y2 = rect
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"\n{label} ({crop.shape[1]}x{crop.shape[0]}):")
    for cnt in sorted(contours, key=lambda c: cv2.boundingRect(c)[0]):
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 2 or h < 4: continue
        char_img = binary[y:y+h, x:x+w]
        # 显示每个模板的得分
        scores = []
        for ch, tmpl in _DIGIT_TEMPLATES.items():
            target_h = max(char_img.shape[0], 6)
            scale = target_h / max(1, tmpl.shape[0])
            if scale < 0.5 or scale > 4.0: continue
            tmpl_scaled = cv2.resize(tmpl, None, fx=scale, fy=scale,
                                      interpolation=cv2.INTER_LINEAR)
            if tmpl_scaled.shape[0] > char_img.shape[0] + 2: continue
            if tmpl_scaled.shape[1] > char_img.shape[1] + 2: continue
            result = cv2.matchTemplate(char_img, tmpl_scaled, cv2.TM_CCOEFF_NORMED)
            scores.append((ch, float(np.max(result))))
        scores.sort(key=lambda x: -x[1])
        top3 = scores[:3]
        ch = _recognize_char(char_img)
        print(f"  ({x:3},{y:2}) {w:2}x{h:2} → {ch:3}  (top3: {top3})")