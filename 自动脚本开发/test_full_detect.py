"""完整检测测试 — 黄点 + 角色名 + YOLO 怪物，全部标注在截图上一张图。

用法:
    python test_full_detect.py [窗口关键词] [地图名]
默认: 冒险岛 魔法密林南郊
"""

import ctypes, ctypes.wintypes, json, sys, time
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent
OUT = PROJECT_DIR / "test_output"
OUT.mkdir(exist_ok=True)


def imwrite(path: Path, img: np.ndarray):
    """OpenCV 5.x 中文路径兜底"""
    _, buf = cv2.imencode(".png", img)
    path.write_bytes(buf.tobytes())


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


def main():
    keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
    map_name = sys.argv[2] if len(sys.argv) >= 3 else "魔法密林南郊"
    print(f"=== 完整检测测试 ===")
    print(f"窗口: '{keyword}'  地图: {map_name}")

    # 1. 找窗口
    matches = [(h, t, l, tp, r, b) for h, t, l, tp, r, b in enum_windows() if keyword in t]
    if not matches: print(f"未找到 '{keyword}'"); return
    hwnd, title, gx, gy, gr, gb = matches[0]
    fw, fh = gr - gx, gb - gy
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    win_title = buf.value.strip()
    print(f"窗口: {win_title}  {fw}x{fh}")

    # 2. 加载 system_setting
    ss_path = PROJECT_DIR / "maps" / "system_setting.json"
    setting = {}
    if ss_path.exists():
        ss = json.loads(ss_path.read_text(encoding="utf-8"))
        entry = ss.get(win_title, {})
        if isinstance(entry, dict): setting = entry
        elif isinstance(entry, list) and len(entry) == 4:
            setting = {"template_rect": entry}
    tmpl_rect = setting.get("template_rect", [85, 728, 150, 745])
    dot_lo = np.array(setting.get("dot_hsv_lower", [25, 100, 180]))
    dot_hi = np.array(setting.get("dot_hsv_upper", [35, 255, 255]))
    print(f"模板区域: {tmpl_rect}")
    print(f"黄点HSV:  [{dot_lo[0]},{dot_lo[1]},{dot_lo[2]}]~[{dot_hi[0]},{dot_hi[1]},{dot_hi[2]}]")

    # 3. 加载地图 mm_region
    map_dir = PROJECT_DIR / "maps" / map_name
    markers = json.loads((map_dir / "markers.json").read_text(encoding="utf-8"))
    mm_region = markers.get(map_name, {}).get("mm_region", [0, 0, 0, 0])
    print(f"小地图区域: {mm_region}")

    # 4. 加载 YOLO
    yolo_path = map_dir / "best.pt"
    if not yolo_path.exists():
        yolo_path = PROJECT_DIR / "maps" / "best.pt"
    print(f"YOLO: {yolo_path} {'✓' if yolo_path.exists() else '✗'}")
    yolo_model = None
    class_names = {}
    if yolo_path.exists():
        from ultralytics import YOLO
        yolo_model = YOLO(str(yolo_path))
        if hasattr(yolo_model, 'names'):
            class_names = yolo_model.names
        elif hasattr(yolo_model.model, 'names'):
            class_names = yolo_model.model.names
        print(f"  类别: {class_names}")

    # 5. 截取
    frame = capture_window(hwnd)
    if frame is None: print("截图失败"); return
    fh, fw = frame.shape[:2]
    print(f"截图: {fw}x{fh}")
    annotated = frame.copy()

    # ---- 6a. 黄点检测 ----
    ml, mt, mr, mb = mm_region
    if mr <= fw and mb <= fh and mr > ml and mb > mt:
        mm = frame[mt:mb, ml:mr].copy()
        hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, dot_lo, dot_hi)
        # 框小地图区域
        cv2.rectangle(annotated, (ml, mt), (mr, mb), (255, 255, 0), 2)
        cv2.putText(annotated, "MINIMAP", (ml, mt - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
        if mask.sum() >= 5:
            num, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
            best, best_sv = None, 0.0
            for i in range(1, num):
                area = stats[i, cv2.CC_STAT_AREA]
                w, h_c = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
                ar = max(w, h_c) / max(1, min(w, h_c))
                if 2 <= area <= 50 and ar <= 1.8:
                    ys, xs = np.where(labels == i)
                    pixels = hsv[ys, xs]
                    sv = float(np.mean(pixels[:, 1])) * float(np.mean(pixels[:, 2]))
                    if sv > best_sv:
                        best_sv = sv
                        best = (centroids[i][0], centroids[i][1])
            if best is not None:
                cx, cy = best
                fx, fy = ml + int(cx), mt + int(cy)
                cv2.circle(annotated, (fx, fy), 10, (0, 255, 255), 2)
                cv2.circle(annotated, (fx, fy), 3, (0, 0, 255), -1)
                cv2.putText(annotated, f"YELLOW DOT ({fx},{fy})", (fx + 14, fy - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2)
                print(f"黄点: ({fx},{fy}) area={area}")
            else:
                print(f"黄点: 有遮罩像素{int(mask.sum())}但未通过筛选")
        else:
            print(f"黄点: 无匹配像素 (遮罩={int(mask.sum())})")
    else:
        print(f"黄点: mm_region 越界 {mm_region} vs {fw}x{fh}")

    # ---- 6b. 角色名模板匹配 ----
    tx, ty, tr, tb = tmpl_rect
    # 边界检查：越界则回退默认
    if tb > fh or tr > fw or ty < 0 or tx < 0 or tb <= ty or tr <= tx:
        print(f"角色名: template_rect {tmpl_rect} 越界 {fw}x{fh}，回退默认")
        tx, ty, tr, tb = (85, 728, 150, 745)
    template = frame[ty:tb, tx:tr].copy()
    th, tw = template.shape[:2]
    if th > 0 and tw > 0:
        # 框模板区域
        cv2.rectangle(annotated, (tx, ty), (tr, tb), (255, 100, 0), 2)
        cv2.putText(annotated, "TEMPLATE", (tx, ty - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 100, 0), 1)
        # 模板匹配
        skip = int(fh * 0.10)
        roi = frame[:fh - skip, :]
        result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > 0.5:
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            cv2.circle(annotated, (cx, cy), 12, (255, 0, 255), 2)
            cv2.circle(annotated, (cx, cy), 4, (255, 0, 0), -1)
            cv2.putText(annotated, f"CHAR ({cx},{cy}) {max_val:.2f}",
                        (cx + 16, cy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 2)
            print(f"角色名: ({cx},{cy}) score={max_val:.2f}")
        else:
            print(f"角色名: 最佳匹配 {max_val:.2f} < 0.5")
    else:
        print(f"角色名: 模板区域为空")

    # ---- 6c. YOLO 怪物检测 ----
    if yolo_model is not None:
        results = yolo_model.predict(frame, conf=0.3, iou=0.5,
                                      device="cpu", verbose=False)
        monster_count = 0
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                name = class_names.get(cls_id, f"c{cls_id}")
                # 非怪物跳过
                non_monster = {"character_name", "ui", "portal", "npc",
                               "rope", "ladder", "platform"}
                if name.lower() in non_monster:
                    continue
                monster_count += 1
                color = (0, 255, 0) if conf > 0.6 else (0, 180, 0)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                cv2.putText(annotated, f"{name} {conf:.2f}",
                            (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        print(f"YOLO: {monster_count} 个怪物")
    else:
        print(f"YOLO: 无模型")

    # 7. 保存
    imwrite(OUT / "full_detect.png", annotated)
    print(f"\n标记图: test_output/full_detect.png")


def _has_dml():
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


if __name__ == "__main__":
    main()
