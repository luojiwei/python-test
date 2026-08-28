"""test_player_pos.py — 角色定位测试脚本

复用自动脚本自身的定位方法：
  1) YOLO + OCR 角色屏幕定位（大地图/主画面）
     ultralytics.YOLO 检测怪物/玩家 → find_character_by_ocr 识别昵称 → 角色屏幕坐标
  2) 小地图黄点定位
     input_utils.capture_frame() → perception.find_yellow_dot → 小地图内位置
  3) 局部滚动定位 → 世界坐标（大地图坐标）
     minimap_tools.locate_in_full_map() + compute_world_pos()
  4) world_model.WorldModel.find_platform() → 所在平台

输出三张标注图到 test_output/：
  full_frame_*.png   全帧：怪物框(红) + 玩家框(蓝) + 角色(黄圈+昵称) + 小地图区域框
  minimap_*.png      小地图放大：黄点标记（小地图位置）
  worldmap_*.png     完整小地图：世界坐标点 + 平台轮廓（世界坐标位置）

用法: python test_player_pos.py [--name 角色名] [--no-yolo]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import config
from input_utils import capture_frame, find_window_by_title, force_foreground
from minimap_tools import compute_world_pos, locate_in_full_map
from perception import (
    _crop_player_name_roi, _ocr_text, _name_match,
    detect_objects, find_character_by_ocr, find_yellow_dot, move_model_to_device,
)
from world_model import WorldModel, load_world_model

TRIM = 4                      # 与 minimap_tools 拼接/定位一致的裁边量
OUT_DIR = SCRIPT_DIR / "test_output"


def _imread_zh(p: Path):
    """中文路径读图（cv2.imread 中文会失败）。"""
    data = np.fromfile(str(p), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _imwrite_zh(p: Path, img: np.ndarray) -> bool:
    """中文路径写图（cv2.imwrite 中文路径会静默返回 False）。"""
    ok, buf = cv2.imencode(".png", img)
    if ok:
        p.write_bytes(buf.tobytes())
    return ok


def _find_map_dir() -> Path:
    """自动发现 maps/{窗口}/{地图}/ 下第一个含 config.json 的地图目录。"""
    maps_root = SCRIPT_DIR / "maps"
    for win_dir in sorted(maps_root.iterdir()):
        if not win_dir.is_dir():
            continue
        for map_dir in sorted(win_dir.iterdir()):
            if (map_dir / "config.json").exists():
                return map_dir
    raise FileNotFoundError(f"未找到地图目录（{maps_root} 下无 config.json）")


def _load_dot_hsv(map_dir: Path, win_title: str):
    """从 system_setting.json 按窗口名读取黄点 HSV 阈值（与 map_loader 同逻辑）。"""
    lower = np.array(config.DOT_HSV_LOWER, dtype=np.uint8)
    upper = np.array(config.DOT_HSV_UPPER, dtype=np.uint8)
    ss_path = map_dir.parent.parent / "system_setting.json"
    if ss_path.exists():
        try:
            data = json.loads(ss_path.read_text(encoding="utf-8"))
            entry = data.get(win_title) or data.get("_default") or {}
            if isinstance(entry, dict):
                lv = entry.get("dot_hsv_lower")
                uv = entry.get("dot_hsv_upper")
                if lv and len(lv) >= 3:
                    lower = np.array(lv, dtype=np.uint8)
                if uv and len(uv) >= 3:
                    upper = np.array(uv, dtype=np.uint8)
        except Exception as e:
            print(f"[警告] 读取 system_setting.json 失败: {e}（使用默认阈值）")
    return lower, upper


def _load_char_name(name_arg: str | None) -> str:
    """角色名来源：命令行参数 > config_cache.json > config.CHARACTER_NAME。"""
    if name_arg and name_arg.strip():
        return name_arg.strip()
    cache = SCRIPT_DIR / "config_cache.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            v = data.get("char_name", "")
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            pass
    return (config.CHARACTER_NAME or "").strip()


def _load_yolo(map_dir: Path):
    """加载 YOLO 模型（与 map_loader 相同：地图专属优先，兜底 maps/best.pt）。"""
    from ultralytics import YOLO
    yolo_path = map_dir / "best.pt"
    fallback_path = map_dir.parent.parent / "best.pt"
    if not yolo_path.exists():
        yolo_path = fallback_path
        print(f"[YOLO] 使用兜底模型: {yolo_path.name}")
    if not yolo_path.exists():
        return None
    model = YOLO(str(yolo_path))
    names = (getattr(model, "names", None)
             or getattr(getattr(model, "model", None), "names", None))
    if names:
        config.CLASS_NAMES.clear()
        config.CLASS_NAMES.update(names)
    move_model_to_device(model)
    n_monster = len(config.MONSTER_CLASS_NAMES)
    print(f"[YOLO] 模型: {yolo_path.name}  {len(config.CLASS_NAMES)}类  "
          f"玩家类别={config.PLAYER_CLASS_NAME}  怪物类别={sorted(config.MONSTER_CLASS_NAMES)}")
    return model


def _crop_extended_roi(frame_bgr: np.ndarray, box: dict,
                       above: int = 100,
                       below: int | None = None) -> np.ndarray | None:
    """调试用：扩大玩家框上方范围（默认 ABOVE=100）以容纳伤害数字上方的昵称。"""
    from config import OCR_UPSCALE, PLAYER_NAME_ROI_BELOW
    h, w = frame_bgr.shape[:2]
    x1 = max(0, int(box["x1"]))
    x2 = min(w, int(box["x2"]))
    y1 = max(0, int(box["y1"]) - above)
    y2 = min(h, int(box["y1"]) + (below if below is not None else PLAYER_NAME_ROI_BELOW))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    roi = frame_bgr[y1:y2, x1:x2]
    return cv2.resize(roi, None, fx=OCR_UPSCALE, fy=OCR_UPSCALE,
                      interpolation=cv2.INTER_CUBIC)


def _find_char_by_extended_roi(frame_bgr: np.ndarray,
                               players: list[dict],
                               char_name: str,
                               above: int = 20,
                               below_pad: int = 5):
    """扩展版 OCR 角色定位：覆盖整个玩家框 + 上方少量像素。

    适用于昵称实际落在玩家框内/附近的场景（默认 ROI 不够大时）。
    返回 (cx, cy, score, box_idx) 或 None。
    """
    if not char_name or not players:
        return None
    best: tuple[int, int, float, int] | None = None
    best_score = 0.0
    for i, p in enumerate(players):
        bh = int(p["y2"]) - int(p["y1"])
        roi = _crop_extended_roi(frame_bgr, p, above=above, below=bh + below_pad)
        if roi is None:
            continue
        text = _ocr_text(roi)
        if not text:
            continue
        score = _name_match(text, char_name)
        if score > best_score:
            best_score = score
            cx = int(p["cx"])
            cy = int((p["y1"] + p["y2"]) / 2)
            best = (cx, cy, score, i)
    return best


def _send_to_bottom(hwnd: int) -> None:
    """截图后把窗口放到最底（不挡其他窗口）。"""
    import ctypes
    HWND_BOTTOM = ctypes.c_void_p(1)
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0010)  # NOMOVE|NOSIZE|NOACTIVATE
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="角色定位测试：YOLO+OCR / 小地图黄点 / 世界坐标")
    parser.add_argument("--name", default=None, help="角色名（默认读 config_cache.json）")
    parser.add_argument("--no-yolo", action="store_true", help="跳过 YOLO+OCR 定位")
    args = parser.parse_args()
    char_name = _load_char_name(args.name)

    OUT_DIR.mkdir(exist_ok=True)

    # ---- 1. 找游戏窗口 ----
    win = find_window_by_title(config.WINDOW_TITLE)
    if win is None:
        print(f"[错误] 未找到窗口 '{config.WINDOW_TITLE}'，请确认游戏已启动")
        return
    hwnd, win_title = win[0], win[1]
    print(f"[1/7] 窗口: '{win_title}'  hwnd={hwnd}")

    # ---- 2. 定位地图资源 ----
    map_dir = _find_map_dir()
    map_cfg = json.loads((map_dir / "config.json").read_text(encoding="utf-8"))
    mm_region = tuple(map_cfg.get("mm_region", [8, 97, 128, 208]))
    wm: WorldModel = load_world_model(str(map_dir / "world_model.json"))
    raw_wm = json.loads((map_dir / "world_model.json").read_text(encoding="utf-8"))
    ms = raw_wm.get("minimap_size", [])
    minimap_size = (int(ms[0]), int(ms[1])) if len(ms) == 2 else (0, 0)
    full_path = map_dir / "minimap_full.png"
    minimap_full = _imread_zh(full_path) if full_path.exists() else None
    if minimap_full is None:
        print(f"[错误] 缺少完整小地图 {full_path}（局部滚动定位需要拼接产物）")
        return
    lower, upper = _load_dot_hsv(map_dir, win_title)
    print(f"[2/7] 地图: {map_dir.parent.name}/{map_dir.name}  "
          f"mm_region={mm_region}  minimap_size={minimap_size}  "
          f"minimap_full={minimap_full.shape[1]}x{minimap_full.shape[0]}")

    # ---- 3. 加载 YOLO 模型 ----
    yolo = None if args.no_yolo else _load_yolo(map_dir)
    if char_name:
        print(f"[3/7] 角色名: '{char_name}'（YOLO 玩家框 → OCR 昵称匹配）")
    else:
        print("[3/7] 未配置角色名（config_cache.json / config），跳过 OCR 定位")

    # ---- 4. 截图（提最前 → 截 → 放最底） ----
    force_foreground(hwnd)
    time.sleep(0.35)
    frame = capture_frame(hwnd)
    _send_to_bottom(hwnd)
    if frame is None:
        print("[错误] 截图失败（capture_frame 返回 None）")
        return
    fh, fw = frame.shape[:2]
    mean_b = float(np.mean(frame))
    if mean_b < 1.0:
        print(f"[警告] 画面几乎全黑 (mean={mean_b:.2f})，PrintWindow 可能截不到 "
              f"DirectX 内容，请确认窗口在前台且未被遮挡")
    print(f"[4/7] 截图: {fw}x{fh}  mean={mean_b:.1f}")

    # 时间戳提前：ROI 调试截图也要用同一时间戳分组
    ts = time.strftime("%H%M%S")

    # ---- 5. YOLO 检测 + OCR 角色屏幕定位 ----
    monsters: list[dict] = []
    players: list[dict] = []
    char_pos = None
    if yolo is not None:
        monsters, players = detect_objects(yolo, frame)
        print(f"[5/7] YOLO: 怪物 {len(monsters)}  玩家 {len(players)}")
        # 每个玩家框都跑 OCR + 打印，便于排查 OCR 失败原因
        ocr_results: list[tuple[int, dict, str, float]] = []
        for i, p in enumerate(players):
            roi = _crop_player_name_roi(frame, p)
            text = _ocr_text(roi) if roi is not None else ""
            score = _name_match(text, char_name) if (char_name and text) else 0.0
            ocr_results.append((i, p, text, score))
            tag = "MATCH" if score >= 0.55 else "    "
            x1, y1, x2, y2 = p["x1"], p["y1"], p["x2"], p["y2"]
            conf = p["conf"]
            print(f"       [默认ROI above=48/below=8] 玩家框 #{i} "
                  f"({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f}) conf={conf:.2f}  "
                  f"OCR={text!r}  score={score:.2f}  {tag}")
            if roi is not None:
                _imwrite_zh(OUT_DIR / f"roi_player_{i}_{ts}.png", roi)
        # 默认方法结果（生产代码 find_character_by_ocr）
        if char_name:
            char_pos_default = find_character_by_ocr(frame, players, char_name)
        else:
            char_pos_default = None
        # 扩展 ROI：覆盖整个玩家框（诊断用，不动 production 代码）
        char_pos_ext = _find_char_by_extended_roi(frame, players, char_name) \
            if char_name else None
        if char_pos_default:
            print(f"       → 默认方法 (auto-script): 屏幕({char_pos_default[0]}, "
                  f"{char_pos_default[1]}) score={char_pos_default[2]:.2f}")
        else:
            print("       → 默认方法 (auto-script): 未匹配到")
        if char_pos_ext:
            print(f"       → 扩展 ROI (test-only):    屏幕({char_pos_ext[0]}, "
                  f"{char_pos_ext[1]}) score={char_pos_ext[2]:.2f}  box#{char_pos_ext[3]}")
        else:
            print("       → 扩展 ROI (test-only):    未匹配到")
        # 主方法 = 自动脚本的（默认 ROI）；扩展 ROI 仅作参考
        char_pos = char_pos_default
    else:
        char_pos_default = None
        char_pos_ext = None
        print("[5/7] 已跳过 YOLO+OCR（--no-yolo）")

    # ---- 6. 小地图黄点 → 世界坐标 ----
    ml, mt, mr, mb = mm_region
    mm = frame[mt:mb, ml:mr].copy() if (mr <= fw and mb <= fh) else None
    if mm is None or mm.size == 0:
        print(f"[错误] 小地图区域越界 mm_region={mm_region} 帧尺寸={fw}x{fh}")
        return
    dot = find_yellow_dot(mm, lower, upper)
    print(f"[6/7] 小地图 {mm.shape[1]}x{mm.shape[0]}  黄点: "
          f"{f'({dot[0]:.1f}, {dot[1]:.1f})' if dot else '未找到'}")
    wx, wy = 0.0, 0.0
    view = None
    platform_id = None
    if dot is not None:
        loc = locate_in_full_map(mm, minimap_full)
        if loc is None:
            print("[警告] 局部小地图在完整小地图上定位失败（匹配分数过低）")
        else:
            view = loc[:2]
            fh_f, fw_f = minimap_full.shape[:2]
            wx, wy = compute_world_pos(dot, loc, (fw_f, fh_f), minimap_size)
            platform_id = wm.find_platform(wx, wy)
    print(f"       世界坐标: ({wx:.1f}, {wy:.1f})  视图偏移={view}  平台={platform_id}")

    # ---- 7. 标注输出 ----

    # 7a. 全帧：怪物/玩家/角色 + 小地图区域
    full_ann = frame.copy()
    cv2.rectangle(full_ann, (ml, mt), (mr, mb), (0, 255, 0), 2)
    cv2.putText(full_ann, "minimap", (ml, mt - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    for m in monsters:
        x1, y1, x2, y2 = (int(m[k]) for k in ("x1", "y1", "x2", "y2"))
        cv2.rectangle(full_ann, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(full_ann, f"{m.get('cls_name', '?')} {m['conf']:.2f}",
                    (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    # 玩家框（YOLO）：不管 OCR 成败都画出，便于看到"游戏角色"在画面中的位置
    for i, p in enumerate(players):
        x1, y1, x2, y2 = (int(p[k]) for k in ("x1", "y1", "x2", "y2"))
        cx, cy = int(p["cx"]), int((p["y1"] + p["y2"]) / 2)
        cv2.rectangle(full_ann, (x1, y1), (x2, y2), (255, 128, 0), 2)
        cv2.putText(full_ann, f"Player#{i} {p['conf']:.2f}", (x1, max(0, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 128, 0), 1)
        cv2.drawMarker(full_ann, (cx, cy), (255, 128, 0),
                       markerType=cv2.MARKER_CROSS, markerSize=14, thickness=2)
    # 默认方法（生产代码 find_character_by_ocr）结果：蓝色虚线圆 + 文字
    if char_pos_default:
        cx, cy, conf = char_pos_default
        cv2.circle(full_ann, (cx, cy), 14, (255, 0, 0), 2, lineType=cv2.LINE_AA)
        cv2.putText(full_ann, f"DEFAULT({conf:.2f})",
                    (cx + 16, cy - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
    # 扩展 ROI（test-only）：橙色实心圆 + 角色名（最显眼）
    if char_pos_ext:
        cx, cy, conf, _ = char_pos_ext
        cv2.circle(full_ann, (cx, cy), 16, (0, 165, 255), 3)
        cv2.putText(full_ann, f"EXT ME: {char_name} ({conf:.2f})",
                    (cx + 18, cy + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2)
    if dot is not None:
        info = f"World({wx:.0f},{wy:.0f}) Plat={platform_id}"
    else:
        info = "Dot not found"
    cv2.putText(full_ann, info, (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
    p_full = OUT_DIR / f"full_frame_{ts}.png"
    _imwrite_zh(p_full, full_ann)

    # 7b. 小地图放大 4x：黄点
    mm_ann = cv2.resize(mm, (mm.shape[1] * 4, mm.shape[0] * 4),
                        interpolation=cv2.INTER_NEAREST)
    if dot is not None:
        dx, dy = int(dot[0] * 4), int(dot[1] * 4)
        cv2.circle(mm_ann, (dx, dy), 10, (0, 255, 255), 2)
        cv2.line(mm_ann, (dx - 14, dy), (dx + 14, dy), (0, 255, 255), 1)
        cv2.line(mm_ann, (dx, dy - 14), (dx, dy + 14), (0, 255, 255), 1)
        cv2.putText(mm_ann, f"Dot({dot[0]:.0f},{dot[1]:.0f})", (6, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
    else:
        cv2.putText(mm_ann, "Dot not found", (6, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    p_mm = OUT_DIR / f"minimap_{ts}.png"
    _imwrite_zh(p_mm, mm_ann)

    # 7c. 完整小地图放大 2x：世界坐标 + 平台轮廓
    wm_ann = cv2.resize(minimap_full, (minimap_full.shape[1] * 2,
                                       minimap_full.shape[0] * 2),
                        interpolation=cv2.INTER_NEAREST)
    for plat in wm.platforms:
        pts = np.array(plat.get("all_points", []), dtype=np.float32)
        if len(pts) == 0:
            continue
        pts = (pts - TRIM) * 2
        cv2.polylines(wm_ann, [pts.astype(np.int32)], False, (255, 255, 0), 1)
    if dot is not None:
        # 世界坐标 → 完整小地图像素：内容区相对框内偏移 TRIM
        px, py = int((wx - TRIM) * 2), int((wy - TRIM) * 2)
        cv2.circle(wm_ann, (px, py), 8, (0, 0, 255), 2)
        cv2.line(wm_ann, (px - 12, py), (px + 12, py), (0, 0, 255), 1)
        cv2.line(wm_ann, (px, py - 12), (px, py + 12), (0, 0, 255), 1)
        cv2.putText(wm_ann, f"World({wx:.0f},{wy:.0f}) Plat={platform_id}",
                    (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    p_wm = OUT_DIR / f"worldmap_{ts}.png"
    _imwrite_zh(p_wm, wm_ann)

    print(f"[7/7] 已保存:")
    print(f"  全帧:     {p_full}")
    print(f"  小地图:   {p_mm}")
    print(f"  大地图:   {p_wm}")
    if yolo is not None and not char_pos_default and not char_pos_ext and char_name:
        print("[提示] 默认+扩展 ROI 都没匹配到角色：可能原因 "
              "① 角色不在画面内 ② config_cache.json 角色名不一致 "
              "③ 玩家框是误检")
    elif yolo is not None and char_pos_default is None and char_pos_ext is not None and char_name:
        print("[提示] 默认 ROI 没匹配，但扩展 ROI 命中 → 自动脚本的 perception.py"
              " ROI 参数偏小（昵称实际在玩家框内）")
    elif yolo is not None and not players and char_name:
        print("[提示] YOLO 未检测到任何玩家框：画面里没有玩家或模型召回不足")
    elif dot is None:
        print("[提示] 黄点未找到：可检查 system_setting.json 的 dot_hsv 阈值，"
              "或用测试脚本验证小地图区域是否正确")


if __name__ == "__main__":
    main()
