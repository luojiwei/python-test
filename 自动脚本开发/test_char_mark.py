"""重新截取游戏窗口，标注真正的角色名位置（头顶浮字，不是底部LV）"""
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
    if "冒险岛" in t: found.append((hwnd, t, r.left, r.top, r.right, r.bottom))
    return True
WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
ctypes.windll.user32.EnumWindows(WEP(cb), 0)
hwnd, title, gl, gt, gr, gb = found[0]

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

# 读取配置（仅用于画搜索范围）
ss_path = Path(__file__).resolve().parent / "maps" / "system_setting.json"
ss = json.loads(ss_path.read_text(encoding="utf-8"))
e = ss.get(title, {})
tx1, ty1, tx2, ty2 = e["template_rect"]
sr = e.get("search_region", [0, 0, w, int(h * 0.9)])

out = img.copy()
# 搜索范围 - 蓝框
cv2.rectangle(out, (sr[0], sr[1]), (sr[2], sr[3]), (255, 0, 0), 2)
cv2.line(out, (0, sr[3]), (w, sr[3]), (255, 255, 0), 2)
cv2.putText(out, f"search ({sr[0]},{sr[1]})-({sr[2]},{sr[3]})", (sr[0] + 5, sr[1] + 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)

# 旧的 template_rect（底部LV）- 黄色虚线
cv2.rectangle(out, (tx1, ty1), (tx2, ty2), (0, 255, 255), 2)
cv2.putText(out, "LV36(误标)", (tx1, ty1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)

# 用旧模板在搜索范围内找最佳匹配
template = img[ty1:ty2, tx1:tx2]
roi = img[sr[1]:sr[3], sr[0]:sr[2]]
result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
_, max_val, _, ml = cv2.minMaxLoc(result)
cx = sr[0] + ml[0] + template.shape[1] // 2
cy = sr[1] + ml[1] + template.shape[0] // 2
cv2.circle(out, (cx, cy), 12, (0, 0, 255), 2)
cv2.putText(out, f"best conf={max_val:.2f}", (cx + 15, cy),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

pp = Path(__file__).resolve().parent / "test_output" / "char_mark.png"
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp.write_bytes(buf.tobytes())
print(f"搜索范围: ({sr[0]},{sr[1]})-({sr[2]},{sr[3]})")
print(f"旧模板(LV36): ({tx1},{ty1})-({tx2},{ty2}) 匹配最佳(搜索区内): conf={max_val:.3f} ({cx},{cy})")
print(f"已保存: {pp}")
