"""minimap_tools.py — 局部滚动小地图解决方案：采集 → 拼接 → 在线定位

背景：当游戏小地图是"局部滚动"模式（只显示完整地图的一部分，随角色移动）时，
黄点在小地图内的位置无法直接换算世界模型坐标（缺视图偏移）。

方案：
  1) 采集（离线）：在游戏里走遍地图，定时截取小地图区域存盘（局部滚动视图序列）
  2) 拼接（离线）：把局部小地图序列拼成一张"完整小地图"参考图
  3) 定位（在线）：运行时把当前局部小地图在完整参考图上做模板匹配，
     得到视图偏移，再叠加黄点位置 → 世界模型坐标

用法:
  python minimap_tools.py collect <地图名>            # 采集（走遍地图后 Ctrl+C 停止）
  python minimap_tools.py stitch <采集输出目录>        # 拼接 → maps/{窗口}/{地图}/minimap_full.png
  python minimap_tools.py test                        # 用整图模拟滑动窗口自测拼接+定位
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import config
from config import PROJECT_DIR
from input_utils import capture_frame, find_window_by_title

# 小地图框在截图中的位置由地图 config.json 的 mm_region 提供
RAW_DIR = PROJECT_DIR / "minimap_raw"          # 采集原始小地图存这里
FULL_NAME = "minimap_full.png"                 # 拼接产物文件名


# ============================================================
# 读取配置
# ============================================================

def _find_map_dir(map_name: str) -> Path:
    """按地图名找到 maps/{窗口}/{地图}/ 目录（跨窗口）。"""
    maps_dir = PROJECT_DIR / "maps"
    for win_dir in sorted(maps_dir.iterdir()):
        if not win_dir.is_dir() or win_dir.name.startswith("."):
            continue
        cand = win_dir / map_name
        if cand.is_dir() and (cand / "config.json").exists():
            return cand
    raise FileNotFoundError(f"未找到地图目录: {map_name}")


def load_mm_region(map_name: str) -> tuple[int, int, int, int]:
    """从地图 config.json 读取小地图区域 (x1,y1,x2,y2)。"""
    map_dir = _find_map_dir(map_name)
    cfg = json.loads((map_dir / "config.json").read_text(encoding="utf-8"))
    mm = cfg.get("mm_region", [8, 97, 128, 208])
    return tuple(mm)


def find_target_hwnd(win_keyword: str = config.WINDOW_TITLE) -> int:
    """按窗口标题关键字找游戏窗口句柄。"""
    win = find_window_by_title(win_keyword)
    if win is None:
        raise RuntimeError(f"未找到窗口: {win_keyword}")
    return win[0]


# ============================================================
# 1. 采集
# ============================================================

def collect(map_name: str, interval: float = 1.0, win_keyword: str = None) -> Path:
    """定时截取小地图区域，保存到 minimap_raw/{地图}/。

    用户在游戏里移动角色走遍地图的每个角落，采集期间小地图滚动，
    相邻截图之间的视图要有重叠（移动慢一点）。
    """
    win_keyword = win_keyword or config.WINDOW_TITLE
    mm = load_mm_region(map_name)
    hwnd = find_target_hwnd(win_keyword)
    out_dir = RAW_DIR / map_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[采集] 窗口={win_keyword}  地图={map_name}  mm_region={mm}")
    print(f"[采集] 保存到 {out_dir}  每 {interval}s 一张，Ctrl+C 停止")
    print("[采集] 请在游戏里慢速走遍地图所有区域（保证相邻截图有重叠）...")

    count = 0
    try:
        while True:
            frame = capture_frame(hwnd)
            if frame is not None:
                x1, y1, x2, y2 = mm
                mm_img = frame[y1:y2, x1:x2]
                if mm_img.size > 0:
                    fname = out_dir / f"mm_{time.strftime('%H%M%S')}_{count:04d}.png"
                    # 中文路径用 imencode 写
                    ok, buf = cv2.imencode(".png", mm_img)
                    if ok:
                        fname.write_bytes(buf.tobytes())
                        count += 1
                        if count % 10 == 0:
                            print(f"  [采集] 已存 {count} 张")
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    print(f"[采集] 完成，共 {count} 张 → {out_dir}")
    return out_dir


# ============================================================
# 2. 拼接
# ============================================================

def _gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _edges(img: np.ndarray) -> np.ndarray:
    """提取边缘轮廓（用于对齐）。小地图边框/面板是平坦区域，边缘图聚焦地形轮廓。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return cv2.Canny(g, 50, 150)


def _overlap_iou(edge_a: np.ndarray, edge_b: np.ndarray,
                 dx: int, dy: int) -> float:
    """两帧边缘在重叠区域的 IoU（b 相对 a 平移 dx,dy 后）。"""
    h, w = edge_a.shape
    x0, x1 = max(0, dx), min(w, w + dx)
    y0, y1 = max(0, dy), min(h, h + dy)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    a_roi = edge_a[y0:y1, x0:x1]
    b_roi = edge_b[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    inter = np.logical_and(a_roi, b_roi).sum()
    union = np.logical_or(a_roi, b_roi).sum()
    return float(inter / max(1, union))


def stitch_minimaps(frames_dir: Path, out_path: Path | None = None,
                    trim: int = 4, min_corr: float = 0.20,
                    min_iou: float = 0.12, quiet: bool = False) -> Path | None:
    """把采集的局部小地图序列拼成完整小地图。

    对齐策略（基于相似轮廓重叠）：
      1) 每帧提取 Canny 边缘轮廓，并裁剪外圈 trim 像素（去掉小地图固定边框/面板，
         避免固定 UI 把相位相关锁死在零平移）
      2) 相邻帧在裁边边缘图上相位相关求平移（边缘图对固定平坦面板免疫，可靠）
      3) 用重叠区域边缘 IoU 验证：轮廓重叠不足的帧拒绝（防错误拼接）
      4) 通过验证的帧加权平均叠入画布（抑制黄点/红点动态元素）
    """
    frames = sorted(frames_dir.glob("mm_*.png"))
    if not frames:
        frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        print(f"[拼接] 目录无图片: {frames_dir}")
        return None

    def _imread_zh(p: Path) -> np.ndarray:
        data = np.fromfile(str(p), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)

    first = _imread_zh(frames[0])
    fh, fw = first.shape[:2]
    eh, ew = fh - 2 * trim, fw - 2 * trim  # 裁剪外圈（去固定边框）后的内容区尺寸
    first_c = first[trim:trim + eh, trim:trim + ew]  # 只拼内容区，去掉边框散布
    margin = max(160, ew)
    canvas = np.zeros((eh + 2 * margin, ew + 2 * margin, 3), dtype=np.float32)
    canvas[margin:margin + eh, margin:margin + ew] = first_c
    weight = np.zeros((eh + 2 * margin, ew + 2 * margin, 1), dtype=np.float32)
    weight[margin:margin + eh, margin:margin + ew] = 1.0
    canvas_edge = np.zeros((eh + 2 * margin, ew + 2 * margin), dtype=np.uint8)
    canvas_edge[margin:margin + eh, margin:margin + ew] = _edges(first_c)

    prev = first
    prev_edge = _edges(prev)[trim:trim + eh, trim:trim + ew]
    prev_pos = [margin, margin]  # 内容区左上角在画布的位置
    placed, skipped = 1, 0

    for i in range(1, len(frames)):
        frame = _imread_zh(frames[i])
        if frame.shape[:2] != (fh, fw):
            continue
        eb = _edges(frame)[trim:trim + eh, trim:trim + ew]

        # 1) 相位相关（裁边边缘图；内容平移，窗口位移符号相反）
        ga = prev_edge.astype(np.float32); ga -= ga.mean()
        gb = eb.astype(np.float32); gb -= gb.mean()
        try:
            (sx, sy), resp = cv2.phaseCorrelate(ga, gb)
        except Exception:
            skipped += 1
            continue
        if resp < min_corr:
            skipped += 1
            if not quiet:
                print(f"  [拼接] 跳过 #{i}（无重叠 响应={resp:.2f}）")
            continue
        bx = prev_pos[0] - int(round(sx))
        by = prev_pos[1] - int(round(sy))

        # 2) 重叠验证：实际平移下的轮廓重叠 IoU
        adx, ady = bx - prev_pos[0], by - prev_pos[1]
        iou = _overlap_iou(prev_edge, eb, adx, ady)
        if iou < min_iou:
            skipped += 1
            if not quiet:
                print(f"  [拼接] 跳过 #{i}（轮廓重叠不足 IoU={iou:.2f} 响应={resp:.2f}）")
            continue

        # 3) 画布扩展
        need = 0
        if bx < 0: need = max(need, -bx)
        if by < 0: need = max(need, -by)
        if bx + ew > canvas.shape[1]: need = max(need, bx + ew - canvas.shape[1])
        if by + eh > canvas.shape[0]: need = max(need, by + eh - canvas.shape[0])
        if need > 0:
            pad = need + margin
            canvas = np.pad(canvas, ((pad, pad), (pad, pad), (0, 0)))
            canvas_edge = np.pad(canvas_edge, ((pad, pad), (pad, pad)))
            weight = np.pad(weight, ((pad, pad), (pad, pad), (0, 0)))
            prev_pos[0] += pad; prev_pos[1] += pad
            bx += pad; by += pad

        # 4) 叠入内容区（加权平均，抑制动态黄点/红点）
        frame_c = frame[trim:trim + eh, trim:trim + ew]
        old = canvas[by:by + eh, bx:bx + ew]
        w_old = weight[by:by + eh, bx:bx + ew]
        new_w = w_old + 1.0
        canvas[by:by + eh, bx:bx + ew] = (old * w_old + frame_c) / new_w
        weight[by:by + eh, bx:bx + ew] = new_w
        canvas_edge[by:by + eh, bx:bx + ew] = _edges(
            canvas[by:by + eh, bx:bx + ew].astype(np.uint8))

        prev = frame
        prev_edge = eb
        prev_pos = [bx, by]
        placed += 1
        if not quiet and placed % 20 == 0:
            print(f"  [拼接] 已放置 {placed}/{len(frames)}")

    # 裁剪掉全零边缘
    canvas_u8 = canvas.astype(np.uint8)
    mask = (canvas_u8.sum(axis=2) > 10).astype(np.uint8)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        print("[拼接] 画布为空")
        return None
    cropped = canvas_u8[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    if out_path is None:
        out_path = frames_dir.parent / "minimap_full.png"
    ok, buf = cv2.imencode(".png", cropped)
    if ok:
        out_path.write_bytes(buf.tobytes())
    print(f"[拼接] 完成: {len(frames)} 张 → {out_path} ({cropped.shape[1]}x{cropped.shape[0]})"
          f"  放置{placed} 跳过{skipped}")
    return out_path


# ============================================================
# 3. 在线定位
# ============================================================

def locate_in_full_map(mm_img: np.ndarray, full_img: np.ndarray,
                       trim: int = 4,
                       min_score: float = 0.35) -> tuple[float, float, float] | None:
    """当前局部小地图在完整小地图上的位置（在线定位）。

    与拼接使用相同的裁边策略：局部图去掉固定边框（内容区），在完整图
    （拼接产物，已是内容区）上用边缘图模板匹配，避免固定 UI 干扰。

    Returns:
        (view_x, view_y, score)：局部小地图原图左上角在完整图中的坐标。
        view_x/view_y 为整数像素；score 为匹配分数。
    """
    if mm_img.shape[0] <= 2 * trim or mm_img.shape[1] <= 2 * trim:
        mm_c, t = mm_img, 0
    else:
        mm_c, t = mm_img[trim:-trim, trim:-trim], trim
    mm_e = _edges(mm_c)
    full_e = _edges(full_img)
    fh, fw = full_e.shape[:2]
    mh, mw = mm_e.shape[:2]
    if mh > fh or mw > fw:
        return None
    res = cv2.matchTemplate(full_e, mm_e, cv2.TM_CCOEFF_NORMED)
    _, mx, _, ml = cv2.minMaxLoc(res)
    if mx < min_score:
        return None
    # 返回"局部图原图左上角"在完整图中的坐标（裁边还原）
    return float(ml[0] - t), float(ml[1] - t), float(mx)


def compute_world_pos(dot: tuple[float, float], view: tuple[float, float, float],
                      full_size: tuple[int, int] = (0, 0),
                      minimap_size: tuple[int, int] = (0, 0),
                      trim: int = 4) -> tuple[float, float]:
    """局部小地图定位 → 世界模型坐标。

    坐标系约定（与拼接一致）：
      - 拼接产物 M 是"内容区"（每帧裁掉外圈 trim 的固定边框后拼接）
      - 世界模型坐标基于小地图框（minimap_size，含边框）
      - 内容区在框内偏移 trim → 世界坐标 = 视图位置 + 黄点 + trim

    Args:
        dot: 黄点在小地图原图内的 (x, y)
        view: locate_in_full_map 返回 (view_x, view_y, score)，即局部图
              原图左上角在拼接产物（内容区）中的坐标
        full_size: 拼接完整小地图尺寸（保留参数，当前不参与缩放）
        minimap_size: 世界模型记录的完整小地图尺寸（保留参数，当前不参与缩放）
        trim: 与拼接/定位一致的裁边量

    Returns:
        (world_x, world_y) 世界模型坐标
    """
    vx, vy, _ = view
    dx, dy = dot
    return vx + dx + trim, vy + dy + trim


# ============================================================
# 自测：用整图模拟局部滚动序列
# ============================================================

def _selftest() -> None:
    """从真实整图小地图模拟滑动窗口序列，验证拼接 + 定位。"""
    shot_dir = PROJECT_DIR.parent / "脚本开发工具" / "YOLO自动标注工具" / "screenshots"
    import glob
    files = sorted(glob.glob(str(shot_dir / "废弃都市_shot_*.png")))
    if not files:
        print("[自测] 无废弃都市截图，跳过")
        return
    def _imread_zh(p):
        data = np.fromfile(p, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    full = _imread_zh(files[0])[68:141, 6:248]  # 242x73 完整小地图
    print(f"[自测] 完整小地图 {full.shape[1]}x{full.shape[0]}")

    # 生成模拟"局部滚动"序列：横向滑动窗口（模拟地铁类横向滚动地图）
    fh, fw = full.shape[:2]
    WW, WH = 120, 60
    sim_dir = RAW_DIR / "_selftest"
    sim_dir.mkdir(parents=True, exist_ok=True)
    xs = list(range(0, fw - WW, 15)) + list(range(fw - WW, -1, -15))
    for i, sx in enumerate(xs):
        sy = 0  # 模拟纯横向滚动
        win = full[sy:sy + WH, sx:sx + WW]
        ok, buf = cv2.imencode(".png", win)
        (sim_dir / f"mm_0000_{i:04d}.png").write_bytes(buf.tobytes())
    print(f"[自测] 生成模拟序列 {len(xs)} 张")

    # 拼接
    stitched = stitch_minimaps(sim_dir, out_path=RAW_DIR / "_selftest_full.png", quiet=True)
    if stitched is None:
        print("[自测] 拼接失败")
        return
    data = np.fromfile(str(stitched), dtype=np.uint8)
    stitched_img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    print(f"[自测] 拼接结果 {stitched_img.shape[1]}x{stitched_img.shape[0]}"
          f"（期望≈{fw}x{fh}）")

    # 定位：每个模拟窗口的"内容区原点"应找回正确世界坐标
    # 窗口内容区左上角（原图 (trim,trim)）在世界模型中的真实位置 = (sx+trim, trim)
    trim = 4
    ok_cnt = 0
    errs = []
    for i, sx in enumerate(xs):
        sy = 0
        win = full[sy:sy + WH, sx:sx + WW]
        loc = locate_in_full_map(win, stitched_img)
        if loc is None:
            continue
        dot = (trim, trim)  # 窗口内容区左上角（原图坐标）
        wx, wy = compute_world_pos(dot, loc, stitched_img.shape[:2][::-1], (fw, fh))
        ex, ey = sx + trim, trim
        err = max(abs(wx - ex), abs(wy - ey))
        errs.append(err)
        if err <= 3:
            ok_cnt += 1
    print(f"[自测] 定位成功 {ok_cnt}/{len(xs)}  最大坐标误差={max(errs) if errs else 0:.1f}px")
    # 定位失败（重叠不足场景）：目标视角是否被拒绝
    no_overlap = stitch_minimaps(sim_dir, out_path=RAW_DIR / "_selftest_full2.png",
                                 quiet=True)
    if no_overlap is not None:
        print(f"[自测] 无重叠拼接产物: {no_overlap.name}")


# ============================================================
# 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="局部滚动小地图工具")
    sub = parser.add_subparsers(dest="cmd")

    p_collect = sub.add_parser("collect", help="采集小地图")
    p_collect.add_argument("map_name")
    p_collect.add_argument("--interval", type=float, default=1.0)

    p_stitch = sub.add_parser("stitch", help="拼接小地图")
    p_stitch.add_argument("frames_dir")

    p_test = sub.add_parser("test", help="自测（模拟数据）")

    args = parser.parse_args()
    if args.cmd == "collect":
        collect(args.map_name, args.interval)
    elif args.cmd == "stitch":
        fd = Path(args.frames_dir)
        out = stitch_minimaps(fd)
        # 若采集目录是 minimap_raw/{地图}，自动复制到地图目录供自动脚本加载
        if out and fd.parent == RAW_DIR:
            try:
                dst = _find_map_dir(fd.name) / FULL_NAME
                shutil.copy2(out, dst)
                print(f"[拼接] 已复制到 {dst}（自动脚本将自动加载）")
            except Exception as e:
                print(f"[拼接] 提示: 未能自动复制到地图目录: {e}，请手动把 {out} 复制到地图目录")
    elif args.cmd == "test":
        _selftest()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
