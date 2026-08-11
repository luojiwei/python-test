"""测试 HP/MP 识别 — 截图 → 裁剪 → 识别 → 显示"""

import ctypes, ctypes.wintypes, json, sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent

# ---- PrintWindow ----
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


# ---- 数字识别 ----
from auto_potion import read_hp_mp

# ---- 主逻辑 ----
def main():
    keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
    matches = [(h, t, l, tp, r, b) for h, t, l, tp, r, b in enum_windows() if keyword in t]
    if not matches: print(f"未找到 '{keyword}'"); return
    hwnd, title, gx, gy, gr, gb = matches[0]
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    win_title = buf.value.strip()
    print(f"窗口: {win_title}  {gr-gx}x{gb-gy}")

    # 读取 system_setting
    ss_path = PROJECT_DIR / "maps" / "system_setting.json"
    hp_rect = mp_rect = None
    if ss_path.exists():
        settings = json.loads(ss_path.read_text(encoding="utf-8"))
        entry = settings.get(win_title, {})
        if isinstance(entry, dict):
            hr = entry.get("hp_rect")
            mr = entry.get("mp_rect")
            if hr and len(hr) == 4: hp_rect = hr
            if mr and len(mr) == 4: mp_rect = mr

    if not hp_rect or not mp_rect:
        print("未找到 hp_rect/mp_rect，请先在标记工具中框选 HP/MP 区域")
        return
    print(f"HP区域: {hp_rect}  MP区域: {mp_rect}")

    # 截图
    frame = capture_window(hwnd)
    if frame is None: print("截图失败"); return
    fh, fw = frame.shape[:2]
    print(f"截图: {fw}x{fh}")

    out = PROJECT_DIR / "test_output"
    out.mkdir(exist_ok=True)

    annotated = frame.copy()

    # HP
    hx1, hy1, hx2, hy2 = hp_rect
    if hx2 <= fw and hy2 <= fh:
        hp_img = frame[hy1:hy2, hx1:hx2]
        cv2.rectangle(annotated, (hx1, hy1), (hx2, hy2), (0, 0, 255), 2)
        hp_result = read_hp_mp(hp_img)
        if hp_result:
            print(f"HP: {hp_result['current']}/{hp_result['max']}")
            cv2.putText(annotated, f"HP:{hp_result['current']}/{hp_result['max']}",
                        (hx1, hy1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 2)
        else:
            print("HP: 识别失败")
            cv2.putText(annotated, "HP: FAIL", (hx1, hy1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 2)

    # MP
    mx1, my1, mx2, my2 = mp_rect
    if mx2 <= fw and my2 <= fh:
        mp_img = frame[my1:my2, mx1:mx2]
        cv2.rectangle(annotated, (mx1, my1), (mx2, my2), (255, 0, 0), 2)
        mp_result = read_hp_mp(mp_img)
        if mp_result:
            print(f"MP: {mp_result['current']}/{mp_result['max']}")
            cv2.putText(annotated, f"MP:{mp_result['current']}/{mp_result['max']}",
                        (mx1, my1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 2)
        else:
            print("MP: 识别失败")
            cv2.putText(annotated, "MP: FAIL", (mx1, my1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 0), 2)

    # 保存
    _, buf = cv2.imencode(".png", annotated)
    (out / "hpmp_test.png").write_bytes(buf.tobytes())
    print(f"\n标注图: test_output/hpmp_test.png")


if __name__ == "__main__":
    main()
