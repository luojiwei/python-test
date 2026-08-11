"""快速诊断：模拟自动脚本的加载和感知流程。"""
import ctypes, ctypes.wintypes, json, sys, time
from pathlib import Path
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent

# PrintWindow 截取
class BITMAPINFOHEADER(ctypes.Structure):
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
    hdc = ctypes.windll.user32.GetDC(hwnd)
    try:
        memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = ctypes.windll.gdi32.SelectObject(memdc, bmp)
        ctypes.windll.user32.PrintWindow(hwnd, memdc, 1)
        bi = ctypes.create_string_buffer(4 * w * h)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight, bmi.biPlanes, bmi.biBitCount = w, -h, 1, 32
        bmi.biSizeImage = 4 * w * h
        ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi, ctypes.byref(bmi), 0)
        img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
        ctypes.windll.gdi32.SelectObject(memdc, old)
        ctypes.windll.gdi32.DeleteObject(bmp)
        ctypes.windll.gdi32.DeleteDC(memdc)
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
map_name = sys.argv[2] if len(sys.argv) >= 3 else "魔法密林南郊"

# 找窗口
matches = [(h, t, l, tp, r, b) for h, t, l, tp, r, b in enum_windows() if keyword in t]
if not matches:
    print(f"未找到窗口 '{keyword}'"); exit(1)
hwnd, title, gx, gy, gr, gb = matches[0]
print(f"窗口: {title} hwnd={hwnd} ({gx},{gy}) {gr-gx}x{gb-gy}")

# 读取窗口标题（用 GetWindowTextW）
buf = ctypes.create_unicode_buffer(256)
ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
actual_title = buf.value.strip()
print(f"实际标题: '{actual_title}'")

# 读取 system_setting.json
ss_path = PROJECT_DIR / "maps" / "system_setting.json"
print(f"\nsystem_setting.json: {ss_path.exists()}")
if ss_path.exists():
    ss = json.loads(ss_path.read_text(encoding="utf-8"))
    entry = ss.get(actual_title, {})
    print(f"  窗口匹配: {'✓' if actual_title in ss else '✗'}")
    print(f"  dot_hsv_lower: {entry.get('dot_hsv_lower')}")
    print(f"  dot_hsv_upper: {entry.get('dot_hsv_upper')}")
    if isinstance(entry, dict):
        hsv_lo = np.array(entry.get("dot_hsv_lower", [25, 100, 180]))
        hsv_hi = np.array(entry.get("dot_hsv_upper", [35, 255, 255]))
    else:
        hsv_lo = np.array([25, 100, 180])
        hsv_hi = np.array([35, 255, 255])
else:
    hsv_lo = np.array([25, 100, 180])
    hsv_hi = np.array([35, 255, 255])

# 读取地图 mm_region
map_dir = PROJECT_DIR / "maps" / map_name
markers = json.loads((map_dir / "markers.json").read_text(encoding="utf-8"))
mm_region = markers.get(map_name, {}).get("mm_region", [0, 0, 0, 0])
print(f"\n地图: {map_name}")
print(f"  mm_region: {mm_region}")

# 截取并检测
frame = capture_window(hwnd)
fh, fw = frame.shape[:2]
print(f"截取: {fw}x{fh}")

# 提取小地图
ml, mt, mr, mb = mm_region
if mr <= ml or mb <= mt or mr > fw or mb > fh:
    print(f"  mm_region 越界! frame={fw}x{fh} region={mm_region}")
    # 用测试中验证过的区域
    ml, mt, mr, mb = 0, 0, 160, 220
    print(f"  改用测试验证区域: [{ml},{mt},{mr},{mb}]")

mm = frame[mt:mb, ml:mr].copy()
print(f"小地图: {mm.shape[1]}x{mm.shape[0]}")

# 黄点检测
import cv2
hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
mask = cv2.inRange(hsv, hsv_lo, hsv_hi)
mp = mask.sum()
print(f"遮罩像素: {mp} (阈值=5)")

if mp >= 5:
    num, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, min(num, 10)):
        area = stats[i, cv2.CC_STAT_AREA]; w = stats[i, cv2.CC_STAT_WIDTH]; h = stats[i, cv2.CC_STAT_HEIGHT]
        ar = max(w,h)/max(1,min(w,h)); cx,cy = centroids[i]
        ok = "✓" if 2 <= area <= 50 and ar <= 1.8 else "✗"
        print(f"  #{i}: area={area} {w}x{h} ar={ar:.2f} ({cx:.0f},{cy:.0f}) {ok}")
else:
    print("  无匹配像素")
    # 诊断
    h_vals = hsv[:,:,0].ravel(); s_vals = hsv[:,:,1].ravel()
    non_gray = s_vals > 5
    if non_gray.sum() > 0:
        print(f"  有色像素: {non_gray.sum()} / {s_vals.size}")
        print(f"  H: [{h_vals[non_gray].min()}-{h_vals[non_gray].max()}]")
        print(f"  S: [{s_vals[non_gray].min()}-{s_vals[non_gray].max()}]")
    else:
        print(f"  全部灰度 S=0")

# 保存截图
_, buf = cv2.imencode(".png", mm)
(Path("test_output") / "quick_mm.png").write_bytes(buf.tobytes())
print(f"\n小地图: test_output/quick_mm.png")
