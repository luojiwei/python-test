"""截新图，用 CCORR_NORMED 定位角色并标注"""
import ctypes, ctypes.wintypes, cv2, numpy as np, json
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

ss = json.loads(Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/maps/system_setting.json").read_text(encoding="utf-8"))
e = ss["冒险岛怀旧服"]
tx1, ty1, tx2, ty2 = e["template_rect"]
sr = e.get("search_region", [4, 67, 1368, 700])

template = img[ty1:ty2, tx1:tx2]
roi = img[sr[1]:sr[3], sr[0]:sr[2]]

# 用 CCORR_NORMED
result = cv2.matchTemplate(roi, template, cv2.TM_CCORR_NORMED)
_, conf, _, ml = cv2.minMaxLoc(result)
cx = sr[0] + ml[0] + template.shape[1] // 2
cy = sr[1] + ml[1] + template.shape[0] // 2
found_char = conf >= 0.70

print(f"匹配: conf={conf:.3f} pos=({cx},{cy}) {'✓' if found_char else '✗'}")

out = img.copy()
cv2.rectangle(out, (sr[0], sr[1]), (sr[2], sr[3]), (255, 0, 0), 2)
cv2.circle(out, (cx, cy), 14, (0, 255, 0) if found_char else (0, 0, 255), 3)
cv2.putText(out, f"conf={conf:.3f}", (cx + 16, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

pp = Path(__file__).resolve().parent / "test_output" / "ccorr_result.png"
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp.write_bytes(buf.tobytes())
print(f"已保存: {pp}")
