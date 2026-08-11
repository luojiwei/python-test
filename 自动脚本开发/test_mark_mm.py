"""小地图标记测试 — PrintWindow 截全帧 → cv2.selectROI 框选 → 显示截取结果。

用法:
    python test_mark_mm.py [窗口关键词]
默认: 冒险岛
"""

import ctypes, ctypes.wintypes, sys
from pathlib import Path
import cv2, numpy as np

OUT = Path(__file__).resolve().parent / "test_output"
OUT.mkdir(exist_ok=True)


def imwrite(path: Path, img: np.ndarray):
    _, buf = cv2.imencode(".png", img)
    path.write_bytes(buf.tobytes())


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


def main():
    keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
    matches = [(h, t, l, tp, r, b) for h, t, l, tp, r, b in enum_windows() if keyword in t]
    if not matches: print(f"未找到 '{keyword}'"); return
    hwnd, title, gx, gy, gr, gb = matches[0]
    fw, fh = gr - gx, gb - gy
    print(f"窗口: {title}  {fw}x{fh}")

    # Step 1: 截取并缩小显示（方便框选）
    frame = capture_window(hwnd)
    if frame is None: print("截图失败"); return
    fh, fw = frame.shape[:2]

    scale = max(1.0, min(2.0, 1600.0 / fw))
    interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_LINEAR
    disp = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=interp)

    # 画当前参考坐标
    from json import load
    maps_file = Path("F:/Program Files/WorkBuddy Works/python-test/脚本开发工具/地图标记工具/marker_output/maps.json")
    if maps_file.exists():
        data = load(open(str(maps_file), "r", encoding="utf-8"))
        for mn, mc in data.items():
            mmr = mc.get("mm_region", [])
            if len(mmr) == 4:
                ml, mt, mr, mb = mmr
                rx1, ry1 = int(ml * scale), int(mt * scale)
                rx2, ry2 = int(mr * scale), int(mb * scale)
                cv2.rectangle(disp, (rx1, ry1), (rx2, ry2), (0, 0, 255), 1)
                cv2.putText(disp, mn, (rx1, ry1 - 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

    win_name = "Drag to select minimap area, press ENTER to confirm"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_name, min(disp.shape[1], 1600), min(disp.shape[0] + 30, 900))
    roi = cv2.selectROI(win_name, disp, False)
    cv2.destroyAllWindows()

    if roi[2] == 0 or roi[3] == 0:
        print("已取消")
        return

    rx, ry, rw, rh = roi
    x1 = int(rx / scale)
    y1 = int(ry / scale)
    x2 = x1 + int(rw / scale)
    y2 = y1 + int(rh / scale)

    print(f"\n框选区域 (原始坐标): [{x1}, {y1}, {x2}, {y2}]  {x2-x1}x{y2-y1}")

    # Step 2: 用框选坐标截取小地图并显示
    mm_full = frame[y1:y2, x1:x2].copy()
    imwrite(OUT / "mm_cropped.png", mm_full)

    # 放大显示截取结果
    zoom = max(1.0, 800.0 / max(mm_full.shape[:2]))
    mm_disp = cv2.resize(mm_full, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    cv2.imshow("Minimap captured - press any key to close", mm_disp)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    print(f"已保存: test_output/mm_cropped.png ({mm_full.shape[1]}x{mm_full.shape[0]})")
    print(f"框选数据: mm_region={[x1,y1,x2,y2]}  minimap_size={[x2-x1,y2-y1]}")


if __name__ == "__main__":
    main()
