"""annotate_batch.py — 对已有截图批量打标记（离线批处理）

复用 test_player_pos.py 的方法：YOLO 检测 / OCR 角色定位 / 小地图黄点 / 世界坐标。
对指定目录下所有匹配 *.png 的截图逐张处理，输出到 输出目录/annotated/：
  {原名}_annotated.png   全帧：怪物(红框) + 玩家(橙框+十字) + 角色(橙大圆+昵称) + 小地图区域
  {原名}_minimap.png     小地图放大 4x：黄点标记
  {原名}_worldmap.png    完整小地图：世界坐标点 + 平台轮廓

用法: python annotate_batch.py [--dir 截图目录] [--name 角色名] [--no-yolo]
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
from minimap_tools import compute_world_pos, locate_in_full_map
from perception import (
    _crop_player_name_roi, _ocr_text, _name_match,
    detect_objects, find_character_by_ocr, find_yellow_dot,
)
from world_model import WorldModel, load_world_model

from test_player_pos import (
    TRIM, _find_map_dir, _imread_zh, _imwrite_zh, _load_char_name,
    _load_dot_hsv, _load_yolo, _crop_extended_roi, _find_char_by_extended_roi,
)

# 自动脚本 mm_region 基于的参考窗口尺寸（地图 config 按此标定）
REF_W, REF_H = 1366, 768


def _scale_mm_region(mm_region: tuple, w: int, h: int) -> tuple:
    """把参考尺寸(1366x768)下的 mm_region 缩放到实际截图尺寸。"""
    sx, sy = w / REF_W, h / REF_H
    return tuple(int(round(v * (sx if i % 2 == 0 else sy)))
                 for i, v in enumerate(mm_region))


def _draw_worldmap(wm, minimap_full, wx, wy, platform_id) -> np.ndarray:
    """完整小地图标注：平台轮廓 + 世界坐标点。"""
    ann = cv2.resize(minimap_full, (minimap_full.shape[1] * 2,
                                    minimap_full.shape[0] * 2),
                     interpolation=cv2.INTER_NEAREST)
    for plat in wm.platforms:
        pts = np.array(plat.get("all_points", []), dtype=np.float32)
        if len(pts) == 0:
            continue
        cv2.polylines(ann, [((pts - TRIM) * 2).astype(np.int32)], False,
                      (255, 255, 0), 1)
    if wx is not None:
        px, py = int((wx - TRIM) * 2), int((wy - TRIM) * 2)
        cv2.circle(ann, (px, py), 8, (0, 0, 255), 2)
        cv2.line(ann, (px - 12, py), (px + 12, py), (0, 0, 255), 1)
        cv2.line(ann, (px, py - 12), (px, py + 12), (0, 0, 255), 1)
        cv2.putText(ann, f"World({wx:.0f},{wy:.0f}) Plat={platform_id}",
                    (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    return ann


def process_one(src: Path, yolo, map_dir: Path, wm: WorldModel,
                minimap_full: np.ndarray, minimap_size: tuple,
                mm_region_ref: tuple, lower, upper,
                char_name: str, out_dir: Path) -> dict:
    """处理单张截图，返回摘要 dict。"""
    frame = _imread_zh(src)
    if frame is None:
        return {"file": src.name, "error": "读取失败"}
    fh, fw = frame.shape[:2]
    mm_region = _scale_mm_region(mm_region_ref, fw, fh)
    ml, mt, mr, mb = mm_region
    mm = frame[mt:mb, ml:mr].copy() if (mr <= fw and mb <= fh) else None

    # ---- YOLO + OCR ----
    monsters, players = [], []
    char_pos_default = char_pos_ext = None
    ocr_detail: list[str] = []
    if yolo is not None:
        monsters, players = detect_objects(yolo, frame)
        if char_name:
            char_pos_default = find_character_by_ocr(frame, players, char_name)
            char_pos_ext = _find_char_by_extended_roi(frame, players, char_name)
            # 打印每个玩家框的扩展 ROI OCR 文本 + 匹配分（便于核对模糊匹配）
            for i, p in enumerate(players):
                bh = int(p["y2"]) - int(p["y1"])
                roi = _crop_extended_roi(frame, p, above=20, below=bh + 5)
                text = _ocr_text(roi) if roi is not None else ""
                score = _name_match(text, char_name) if text else 0.0
                ocr_detail.append(f"P{i}:OCR={text!r} score={score:.2f}")

    # ---- 小地图黄点 → 世界坐标 ----
    dot = find_yellow_dot(mm, lower, upper) if mm is not None else None
    wx, wy, platform_id = None, None, None
    if dot is not None:
        loc = locate_in_full_map(mm, minimap_full)
        if loc is not None:
            fh_f, fw_f = minimap_full.shape[:2]
            wx, wy = compute_world_pos(dot, loc, (fw_f, fh_f), minimap_size)
            platform_id = wm.find_platform(wx, wy)

    # ---- 标注 ----
    ann = frame.copy()
    # 怪物红框
    for m in monsters:
        x1, y1, x2, y2 = (int(m[k]) for k in ("x1", "y1", "x2", "y2"))
        cv2.rectangle(ann, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(ann, f"{m.get('cls_name', '?')} {m['conf']:.2f}",
                    (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (0, 0, 255), 1)
    # 玩家框 + 中心十字
    for i, p in enumerate(players):
        x1, y1, x2, y2 = (int(p[k]) for k in ("x1", "y1", "x2", "y2"))
        cx, cy = int(p["cx"]), int((p["y1"] + p["y2"]) / 2)
        cv2.rectangle(ann, (x1, y1), (x2, y2), (255, 128, 0), 2)
        cv2.putText(ann, f"Player#{i} {p['conf']:.2f}", (x1, max(0, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 128, 0), 1)
        cv2.drawMarker(ann, (cx, cy), (255, 128, 0),
                       markerType=cv2.MARKER_CROSS, markerSize=14, thickness=2)
    # 默认方法结果（蓝色虚线）
    if char_pos_default:
        cx, cy, conf = char_pos_default
        cv2.circle(ann, (cx, cy), 14, (255, 0, 0), 2, lineType=cv2.LINE_AA)
        cv2.putText(ann, f"DEFAULT({conf:.2f})",
                    (cx + 16, cy - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 0, 0), 2)
    # 扩展 ROI 命中（橙色大圆 + 昵称；仅 score ≥ 阈值才画）
    if char_pos_ext and char_pos_ext[2] >= 0.55:
        cx, cy, conf, _ = char_pos_ext
        cv2.circle(ann, (cx, cy), 16, (0, 165, 255), 3)
        cv2.putText(ann, f"EXT ME: {char_name} ({conf:.2f})",
                    (cx + 18, cy + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 165, 255), 2)
    elif char_pos_ext:
        # 低分候选：在玩家框上标小字提示分数（不画大圆）
        for p in players:
            cx = int(p["cx"])
            cy = int((p["y1"] + p["y2"]) / 2)
            cv2.putText(ann, f"?{char_pos_ext[2]:.2f}",
                        (cx - 8, cy - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (128, 128, 128), 1)
    # 小地图区域框
    cv2.rectangle(ann, (ml, mt), (mr, mb), (0, 255, 0), 2)
    cv2.putText(ann, "minimap", (ml, max(0, mt - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
    # 顶部信息
    if wx is not None:
        info = f"World({wx:.0f},{wy:.0f}) Plat={platform_id}"
    elif dot is None:
        info = "Dot not found"
    else:
        info = "Minimap locate fail"
    cv2.putText(ann, info, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (0, 255, 255), 2)

    stem = src.stem
    _imwrite_zh(out_dir / f"{stem}_annotated.png", ann)
    # 完整小地图（大地图标注）
    wm_ann = _draw_worldmap(wm, minimap_full, wx, wy, platform_id)
    _imwrite_zh(out_dir / f"{stem}_worldmap.png", wm_ann)

    return {
        "file": src.name,
        "frame": f"{fw}x{fh}",
        "monsters": len(monsters),
        "players": len(players),
        "default_ocr": f"{char_pos_default[2]:.2f}" if char_pos_default else "-",
        "ext_ocr": f"{char_pos_ext[2]:.2f}" if char_pos_ext else "-",
        "ocr_detail": " ".join(ocr_detail),
        "dot": f"({dot[0]:.0f},{dot[1]:.0f})" if dot else "-",
        "world": f"({wx:.0f},{wy:.0f})" if wx is not None else "-",
        "platform": platform_id if platform_id else "-",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="批量截图标记（离线）")
    parser.add_argument("--dir", default=None, help="截图目录（默认 test_output）")
    parser.add_argument("--name", default=None, help="角色名")
    parser.add_argument("--no-yolo", action="store_true", help="跳过 YOLO+OCR")
    args = parser.parse_args()

    src_dir = Path(args.dir) if args.dir else SCRIPT_DIR / "test_output"
    out_dir = src_dir / "annotated"
    out_dir.mkdir(exist_ok=True)
    files = sorted(p for p in src_dir.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp")
                   and not p.name.startswith("_"))
    if not files:
        print(f"[错误] {src_dir} 下无图片")
        return
    print(f"[1/5] 待处理 {len(files)} 张: {src_dir}")

    # 地图资源（复用 test_player_pos 的发现逻辑）
    map_dir = _find_map_dir()
    map_cfg = json.loads((map_dir / "config.json").read_text(encoding="utf-8"))
    mm_region_ref = tuple(map_cfg.get("mm_region", [8, 97, 128, 208]))
    wm: WorldModel = load_world_model(str(map_dir / "world_model.json"))
    raw_wm = json.loads((map_dir / "world_model.json").read_text(encoding="utf-8"))
    ms = raw_wm.get("minimap_size", [])
    minimap_size = (int(ms[0]), int(ms[1])) if len(ms) == 2 else (0, 0)
    minimap_full = _imread_zh(map_dir / "minimap_full.png")
    if minimap_full is None:
        print("[错误] 缺少完整小地图 minimap_full.png")
        return
    # 黄点 HSV：无窗口句柄，用 system_setting 里第一个窗口的配置
    ss_path = map_dir.parent.parent / "system_setting.json"
    win_title = ""
    if ss_path.exists():
        try:
            win_title = next(iter(json.loads(
                ss_path.read_text(encoding="utf-8"))))
        except Exception:
            pass
    lower, upper = _load_dot_hsv(map_dir, win_title)
    char_name = _load_char_name(args.name)
    print(f"[2/5] 地图: {map_dir.parent.name}/{map_dir.name}  "
          f"mm_region_ref={mm_region_ref}  minimap_size={minimap_size}  "
          f"minimap_full={minimap_full.shape[1]}x{minimap_full.shape[0]}  "
          f"黄点HSV={lower.tolist()}/{upper.tolist()}  角色={char_name!r}")

    # YOLO 模型（只加载一次，10 张图复用）
    yolo = None if args.no_yolo else _load_yolo(map_dir)
    print(f"[3/5] 开始逐张处理（YOLO 推理可能较慢，请耐心）...")

    t0 = time.time()
    results = []
    for i, f in enumerate(files, 1):
        r = process_one(f, yolo, map_dir, wm, minimap_full, minimap_size,
                        mm_region_ref, lower, upper, char_name, out_dir)
        results.append(r)
        if "error" in r:
            print(f"  [{i}/{len(files)}] {r['file']}: {r['error']}")
        else:
            print(f"  [{i}/{len(files)}] {r['file']}  {r['frame']}  "
                  f"怪{r['monsters']}/玩{r['players']}  OCR默认={r['default_ocr']}  "
                  f"OCR扩展={r['ext_ocr']}  黄点={r['dot']}  世界={r['world']}  "
                  f"平台={r['platform']}")
            if r.get("ocr_detail"):
                print(f"        {r['ocr_detail']}")
    print(f"[4/5] 完成 {len(results)} 张，用时 {time.time()-t0:.1f}s → {out_dir}")

    # 汇总表
    print("\n[5/5] 汇总:")
    print(f"{'文件':<40}{'怪':>3}{'玩':>3}{'OCR默认':>8}{'OCR扩展':>8}"
          f"{'黄点':>14}{'世界坐标':>16}{'平台':>12}")
    for r in results:
        print(f"{r['file']:<40}{r['monsters']:>3}{r['players']:>3}"
              f"{r['default_ocr']:>8}{r['ext_ocr']:>8}{r['dot']:>14}"
              f"{r['world']:>16}{r['platform']:>12}")


if __name__ == "__main__":
    main()
