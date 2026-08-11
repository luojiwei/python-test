"""黄点检测诊断 — PrintWindow 后台截小地图，标记黄点。

用法:
    python test_yellow_dot.py [窗口关键词]
"""

import ctypes
import ctypes.wintypes
import sys
import time
from pathlib import Path

import cv2
import mss
import numpy as np

OUT = Path(__file__).resolve().parent / "test_output"


def imwrite(path: Path, img: np.ndarray):
    """OpenCV 5.x 不支持中文路径，用 imencode 绕过。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _, buf = cv2.imencode(".png", img)
    path.write_bytes(buf.tobytes())


def force_foreground(hwnd):
    """强制将窗口提到最前。"""
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.c_ulong())
    attached = cur != tgt
    if attached:
        ctypes.windll.user32.AttachThreadInput(cur, tgt, True)
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur, tgt, False)


def force_background(hwnd):
    """将窗口强制置底。"""
    HWND_BOTTOM = 1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_BOTTOM, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def enum_all_windows():
    """枚举所有窗口，包括最小化和隐藏的。"""
    found = []
    def cb(hwnd, _):
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 100 or h < 80:
            return True
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value.strip()
        if not title:
            return True
        is_visible = ctypes.windll.user32.IsWindowVisible(hwnd)
        is_iconic = ctypes.windll.user32.IsIconic(hwnd)
        found.append((hwnd, title, r.left, r.top, r.right, r.bottom, is_visible, is_iconic))
        return True
    WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WEP(cb), 0)
    found.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    return found


# ---- PrintWindow 截取工具 ----

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def capture_window_direct(hwnd):
    """用 PrintWindow 直接截取窗口内容（不受遮挡影响）。"""
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None

    hdc = ctypes.windll.user32.GetDC(hwnd)
    if not hdc:
        return None

    try:
        memdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = ctypes.windll.gdi32.SelectObject(memdc, bmp)

        PW_CLIENTONLY = 1
        if not ctypes.windll.user32.PrintWindow(hwnd, memdc, PW_CLIENTONLY):
            # 回退到无 FLAG 的 PrintWindow
            ctypes.windll.user32.PrintWindow(hwnd, memdc, 0)

        # 从 bitmap 读取像素
        bi = ctypes.create_string_buffer(4 * w * h)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # 负值 = top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        bmi.biSizeImage = 4 * w * h
        ctypes.windll.gdi32.GetDIBits(memdc, bmp, 0, h, bi,
                                       ctypes.byref(bmi), 0)
        img = np.frombuffer(bi, dtype=np.uint8).reshape(h, w, 4)
        img = img[:, :, :3].copy()  # BGRA → BGR

        ctypes.windll.gdi32.SelectObject(memdc, old)
        ctypes.windll.gdi32.DeleteObject(bmp)
        ctypes.windll.gdi32.DeleteDC(memdc)
    finally:
        ctypes.windll.user32.ReleaseDC(hwnd, hdc)

    return img


# ---- 放宽的 HSV 检测参数（测试用） ----
DOT_HSV_LO = np.array([22, 15, 120])
DOT_HSV_HI = np.array([40, 255, 255])


def find_yellow_dot_relaxed(mm_bgr: np.ndarray):
    """用放宽参数检测黄点，返回 (cx, cy) 和详细连通域信息。"""
    hsv = cv2.cvtColor(mm_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, DOT_HSV_LO, DOT_HSV_HI)
    mp = mask.sum()

    if mp < 5:
        return None, hsv, mask, []

    num, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num <= 1:
        return None, hsv, mask, []

    best = None
    best_sv = 0.0
    comps = []

    for i in range(1, num):
        area = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        ar = max(w, h) / max(1, min(w, h))
        cx, cy = centroids[i]
        ys, xs = np.where(labels == i)
        pixels = hsv[ys, xs]
        sv = float(np.mean(pixels[:, 1])) * float(np.mean(pixels[:, 2]))
        comps.append({"i": i, "area": area, "w": w, "h": h, "ar": ar,
                       "cx": cx, "cy": cy, "sv": sv})
        if 2 <= area <= 80 and ar <= 2.0:
            if sv > best_sv:
                best_sv = sv
                best = (cx, cy)

    return best, hsv, mask, comps


def main():
    keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
    print(f"=== 黄点检测诊断 ===")
    print(f"关键词: '{keyword}'")

    # 找窗口
    windows = enum_all_windows()
    kw = keyword.lower()
    matches = [(hwnd, title, l, t, r, b, vis, iconic)
               for hwnd, title, l, t, r, b, vis, iconic in windows
               if kw in title.lower()]
    matches.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)

    if not matches:
        print(f"未找到包含 '{keyword}' 的窗口")
        return

    hwnd, title, gx, gy, gr, gb, vis, iconic = matches[0]
    fw, fh = gr - gx, gb - gy
    print(f"窗口: {title}  ({gx},{gy}) {fw}x{fh}")

    # 直接用 PrintWindow 截取（不受遮挡影响）
    frame = capture_window_direct(hwnd)
    if frame is None:
        print("PrintWindow 截取失败，尝试置前后 mss 截取...")
        force_foreground(hwnd)
        time.sleep(0.5)
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        with mss.MSS() as sct:
            region = {"left": r.left, "top": r.top,
                      "width": r.right - r.left, "height": r.bottom - r.top}
            frame = np.array(sct.grab(region))[:, :, :3]
        force_background(hwnd)
        if frame is None:
            print("所有截取方式均失败")
            return

    fh, fw = frame.shape[:2]
    print(f"截取成功: {fw}x{fh}")

    # ---- 只截小地图区域 ----
    for name, mm_rect in [
        ("小地图_(0_160_220)", (0, 0, 160, 220)),
    ]:
        ml, mt, mr, mb = mm_rect
        if mr > fw or mb > fh:
            continue
        mm = frame[mt:mb, ml:mr].copy()

        result, hsv, mask, comps = find_yellow_dot_relaxed(mm)
        safe = name.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "").replace("-", "_")

        mp = mask.sum()
        print(f"小地图 [{ml},{mt},{mr},{mb}] → {mr-ml}x{mb-mt}  遮罩={mp}")
        for c in comps:
            passed = "✓" if (2 <= c["area"] <= 80 and c["ar"] <= 2.0) else "✗"
            print(f"  #{c['i']}: area={c['area']} {c['w']}x{c['h']} ar={c['ar']:.2f} ({c['cx']:.0f},{c['cy']:.0f}) {passed}")

        if result is not None:
            cx, cy = result
            print(f"→ 黄点: ({cx:.0f}, {cy:.0f})")
            mm_marked = mm.copy()
            cv2.circle(mm_marked, (int(cx), int(cy)), 8, (0, 255, 255), 2)
            cv2.circle(mm_marked, (int(cx), int(cy)), 3, (0, 0, 255), -1)
            cv2.putText(mm_marked, f"({cx:.0f},{cy:.0f})", (int(cx) + 10, int(cy) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
            imwrite(OUT / "minimap_marked.png", mm_marked)
            print(f"→ 已保存: test_output/minimap_marked.png")
        else:
            print(f"→ 未找到黄点")


if __name__ == "__main__":
    main()
