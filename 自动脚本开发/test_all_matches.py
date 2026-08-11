"""详细分析模板匹配：分别测模板在游戏世界各处匹配度"""
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
sr = e.get("search_region", [0, 0, w, int(h * 0.9)])

# 模板
template = img[ty1:ty2, tx1:tx2]
print(f"模板: ({tx1},{ty1})-({tx2},{ty2}) {template.shape[1]}x{template.shape[0]}")

# 全帧匹配找所有高分匹配（>0.5）
full_result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
threshold = 0.4
ys, xs = np.where(full_result >= threshold)
print(f"\n全帧匹配分数 >= {threshold} 的位置（按 conf 降序）:")
matches = []
for y, x in zip(ys, xs):
    matches.append((full_result[y, x], x, y))
matches.sort(reverse=True)
for conf, x, y in matches[:10]:
    cx = x + template.shape[1] // 2
    cy = y + template.shape[0] // 2
    in_sr = sr[0] <= cx <= sr[2] and sr[1] <= cy <= sr[3]
    print(f"  conf={conf:.3f} pos=({cx},{cy}) {'在搜索范围内' if in_sr else '在搜索范围外'}")

# 检查玩家名位置（红色箭头指的）
# 画图标注
out = img.copy()
cv2.rectangle(out, (sr[0], sr[1]), (sr[2], sr[3]), (255, 0, 0), 2)
cv2.line(out, (0, sr[3]), (w, sr[3]), (255, 255, 0), 2)

for conf, x, y in matches[:5]:
    cx = x + template.shape[1] // 2
    cy = y + template.shape[0] // 2
    cv2.circle(out, (cx, cy), 8, (0, 255, 255), 2)
    cv2.putText(out, f"{conf:.2f}", (cx + 10, cy - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

pp = Path(__file__).resolve().parent / "test_output" / "all_matches.png"
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp.write_bytes(buf.tobytes())
print(f"\n已保存: {pp}")