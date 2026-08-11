"""实时截取游戏窗口，标注角色名位置"""
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

# 找窗口
found = []
def cb(hwnd, _):
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w < 300 or h < 200:
        return True
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    t = buf.value.strip()
    if "冒险岛" in t:
        found.append((hwnd, t, r.left, r.top, r.right, r.bottom))
    return True

WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
ctypes.windll.user32.EnumWindows(WEP(cb), 0)
if not found:
    print("未找到窗口")
    exit()
hwnd, title, gl, gt, gr, gb = found[0]

# PrintWindow 截取
r = ctypes.wintypes.RECT()
ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
w, h = r.right - r.left, r.bottom - r.top
hdc = ctypes.windll.user32.GetDC(hwnd)
memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
old = ctypes.windll.gdi32.SelectObject(memdc, bmp)
ctypes.windll.user32.PrintWindow(hwnd, memdc, 1)
bi = ctypes.create_string_buffer(4 * w * h)
bmi = _BIH()
bmi.biSize = ctypes.sizeof(_BIH)
bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = w, -h, 1, 32
bmi.biSizeImage = 4 * w * h
ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi, ctypes.byref(bmi), 0)
img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
ctypes.windll.gdi32.SelectObject(memdc, old)
ctypes.windll.gdi32.DeleteObject(bmp)
ctypes.windll.gdi32.DeleteDC(memdc)
ctypes.windll.user32.ReleaseDC(hwnd, hdc)

print(f"{title}  {w}x{h}")

# 读取配置
ss_path = PROJECT_DIR = Path(__file__).resolve().parent / "maps" / "system_setting.json"
ss = json.loads(ss_path.read_text(encoding="utf-8"))
e = ss.get(title, {})
tx1, ty1, tx2, ty2 = e["template_rect"]
sr = e.get("search_region", [0, 0, w, int(h * 0.9)])

# 模板匹配
template = img[ty1:ty2, tx1:tx2]
result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
_, _, _, ml = cv2.minMaxLoc(result)
cx = ml[0] + template.shape[1] // 2
cy = ml[1] + template.shape[0] // 2

# 标注
out = img.copy()
cv2.rectangle(out, (sr[0], sr[1]), (sr[2], sr[3]), (255, 0, 0), 2)  # 搜索范围
cv2.line(out, (0, sr[3]), (w, sr[3]), (255, 255, 0), 2)  # 搜索底边
cv2.rectangle(out, (tx1, ty1), (tx2, ty2), (0, 255, 255), 2)  # 模板区域
cv2.circle(out, (cx, cy), 12, (0, 0, 255), 3)  # 角色名位置
cv2.putText(out, f"role({cx},{cy})", (cx + 15, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
cv2.putText(out, f"search_bot y={sr[3]}", (sr[0], sr[3] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

pp = ProjectDir = Path(__file__).resolve().parent / "test_output" / "live_char.png"
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp.write_bytes(buf.tobytes())
print(f"角色=({cx},{cy})  搜索底边={sr[3]}  模板底边={ty2}")
print(f"已保存: {pp}")
