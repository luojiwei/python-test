"""截游戏窗口，用角色名截图做模板匹配"""
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
hwnd, fw, fh = found[0]

# 截图
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
print(f"截图: {fw}x{fh}")

# 读角色名截图（游戏世界浮字）
name_img = cv2.imread("C:/Users/Administrator/.workbuddy/clipboard-images/clipboard-2026-08-08T06-49-07-484Z-eb3c6396.png")
print(f"角色名图: {name_img.shape[1]}x{name_img.shape[0]}")

# 角色名截图是 1920x1079 分辨率下截的，游戏窗口是 1382x807
# 全屏截图里字符大小需要缩放
scale_ratio = fw / 1920  # 1382/1920 ≈ 0.72
# 实际上角色名截图是裁剪后的，直接当模板用
# 需要缩放到游戏窗口分辨率
v_scale = fh / 1079  # 807/1079 ≈ 0.75
new_w = int(name_img.shape[1] * v_scale)
new_h = int(name_img.shape[0] * v_scale)
template = cv2.resize(name_img, (new_w, new_h), interpolation=cv2.INTER_AREA)
print(f"缩放模板: {new_w}x{new_h}")

# 全帧匹配
result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
_, max_val, _, ml = cv2.minMaxLoc(result)
cx = ml[0] + new_w // 2
cy = ml[1] + new_h // 2
print(f"最佳匹配: conf={max_val:.3f} pos=({cx},{cy})")

# 标注
out = img.copy()
cv2.rectangle(out, (ml[0], ml[1]), (ml[0]+new_w, ml[1]+new_h), (0, 0, 255), 2)
cv2.circle(out, (cx, cy), 10, (0, 0, 255), 3)
cv2.putText(out, f"conf={max_val:.3f}", (cx+15, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)

pp = Path(__file__).resolve().parent / "test_output" / "match_name.png"
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp.write_bytes(buf.tobytes())
print(f"已保存: {pp}")
