"""地图标记工具 — 小地图合成。

解决"局部滚动小地图"：在游戏里走动并逐次截图小地图（mm_region），
再合成为一张完整小地图，保存为该地图的完整小地图（供自动脚本定位用）。

流程：截图（多次）→ 合成（弹窗显示结果）→ 保存。
"""

import json
import time
import tkinter as tk
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk

try:
    from .config import OUTPUT_DIR, _safe_filename
    from .window_utils import capture_client
except ImportError:  # 直接运行（非包）时
    from config import OUTPUT_DIR, _safe_filename  # type: ignore[no-redef]
    from window_utils import capture_client  # type: ignore[no-redef]

# 完整小地图拼接/定位统一裁边量（与 stitch_minimaps 默认 trim 一致）
MINIMAP_TRIM = 4


def _shots_dir(app) -> Path:
    """截图保存目录: marker_output/{窗口}/{地图}_minimap_shots/"""
    win = _safe_filename(app._window_var.get().strip())
    mp = _safe_filename(app.map_name_var.get().strip())
    d = OUTPUT_DIR / win / f"{mp}_minimap_shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _full_map_path(app) -> Path:
    """完整小地图保存路径: marker_output/{窗口}/{地图}_minimap_full.png"""
    win = _safe_filename(app._window_var.get().strip())
    mp = _safe_filename(app.map_name_var.get().strip())
    return OUTPUT_DIR / win / f"{mp}_minimap_full.png"


# ============================================================
# 拼接核心（相位相关求平移 + 局部窗口校正 + 加权平均叠入）
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


def stitch_minimaps(frames: list[np.ndarray],
                    trim: int = 4,
                    min_corr: float = 0.20,
                    min_iou: float = 0.12,
                    quiet: bool = True) -> np.ndarray | None:
    """把局部小地图帧序列拼成完整小地图（BGR ndarray）。

    对齐策略（基于相似轮廓重叠）：
      1) 每帧提取 Canny 边缘轮廓，并裁剪外圈 trim 像素（去掉小地图固定边框/面板，
         避免固定 UI 把相位相关锁死在零平移）
      2) 相邻帧在裁边边缘图上相位相关求平移（边缘图对固定平坦面板免疫，可靠）
      3) 用重叠区域边缘 IoU 验证：轮廓重叠不足的帧拒绝（防错误拼接）
      4) 通过验证的帧加权平均叠入画布（抑制黄点/红点动态元素）
    """
    if not frames:
        return None
    fh, fw = frames[0].shape[:2]
    eh, ew = fh - 2 * trim, fw - 2 * trim  # 裁剪外圈（去固定边框）后的内容区尺寸
    first_c = frames[0][trim:trim + eh, trim:trim + ew]  # 只拼内容区，去掉边框散布
    margin = max(160, ew)
    canvas = np.zeros((eh + 2 * margin, ew + 2 * margin, 3), dtype=np.float32)
    canvas[margin:margin + eh, margin:margin + ew] = first_c
    weight = np.zeros((eh + 2 * margin, ew + 2 * margin, 1), dtype=np.float32)
    weight[margin:margin + eh, margin:margin + ew] = 1.0
    canvas_edge = np.zeros((eh + 2 * margin, ew + 2 * margin), dtype=np.uint8)
    canvas_edge[margin:margin + eh, margin:margin + ew] = _edges(first_c)

    prev = frames[0]
    prev_edge = _edges(prev)[trim:trim + eh, trim:trim + ew]
    prev_pos = [margin, margin]  # 内容区左上角在画布的位置
    placed, skipped = 1, 0

    for i in range(1, len(frames)):
        frame = frames[i]
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

    # 裁剪空白边缘
    canvas_u8 = canvas.astype(np.uint8)
    mask = (canvas_u8.sum(axis=2) > 10).astype(np.uint8)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    result = canvas_u8[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if not quiet:
        print(f"[拼接] 放置{placed} 跳过{skipped} 尺寸={result.shape[1]}x{result.shape[0]}")
    return result


# ============================================================
# 在线定位与坐标换算（与自动脚本 minimap_tools.py 同算法）
# ============================================================

def locate_in_full_map(mm_img: np.ndarray, full_img: np.ndarray,
                       trim: int = MINIMAP_TRIM,
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
                      trim: int = MINIMAP_TRIM) -> tuple[float, float]:
    """局部小地图定位 → 世界模型坐标。

    坐标系约定（与拼接一致）：
      - 拼接产物（minimap_full.png）是"内容区"（每帧裁掉外圈 trim 的固定边框后拼接）
      - 世界模型坐标基于小地图框（含边框）
      - 内容区在框内偏移 trim → 世界坐标 = 视图位置 + 黄点 + trim

    Args:
        dot: 黄点在小地图原图内的 (x, y)
        view: locate_in_full_map 返回 (view_x, view_y, score)
        trim: 与拼接/定位一致的裁边量

    Returns:
        (world_x, world_y) 世界模型坐标
    """
    vx, vy, _ = view
    dx, dy = dot
    return vx + dx + trim, vy + dy + trim


def load_full_minimap(win_name: str, map_name: str) -> dict | None:
    """加载该地图的完整小地图（需已通过小地图合成保存）。

    Returns:
        {
            "content": 内容区 BGR ndarray,
            "full_pil": 补边后的完整框 PIL Image（作为标记背景，尺寸=world_size）,
            "world_size": (w, h) 完整框尺寸（内容区 + 2*trim）,
            "trim": 裁边量,
        }
        或 None（无完整小地图 / 旧格式未带 meta）。
    """
    win = _safe_filename(win_name)
    mp = _safe_filename(map_name)
    full_path = OUTPUT_DIR / win / f"{mp}_minimap_full.png"
    meta_path = OUTPUT_DIR / win / f"{mp}_minimap_full_meta.json"
    if not full_path.exists():
        return None
    data = np.fromfile(str(full_path), dtype=np.uint8)
    content = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if content is None:
        return None
    # 元数据标记新格式（内容区）；旧格式（含边框散布）无 meta，拒绝使用
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        trim = int(meta.get("trim", MINIMAP_TRIM))
    except Exception:
        trim = MINIMAP_TRIM
    h, w = content.shape[:2]
    full_box = cv2.copyMakeBorder(content, trim, trim, trim, trim,
                                  cv2.BORDER_REPLICATE)
    return {
        "content": content,
        "full_pil": Image.fromarray(full_box[:, :, ::-1]),
        "world_size": (w + 2 * trim, h + 2 * trim),
        "trim": trim,
    }


# ============================================================
# 弹窗
# ============================================================

def open_minimap_stitch(app) -> None:
    """打开小地图合成弹窗。"""
    if app.running:
        app.status_text.set("标记运行中，请先停止")
        return
    if not app.map_confirmed:
        app.status_text.set("请先输入地图名称并点击确定")
        return
    if app.target_hwnd is None:
        app.status_text.set("请先选择游戏窗口")
        return
    ml, mt, mr, mb = app.mm_offsets
    if mr - ml <= 0 or mb - mt <= 0:
        app.status_text.set("请先标记小地图")
        return

    # 若已有弹窗则先关掉
    old = getattr(app, "_mms_window", None)
    if old is not None and old.winfo_exists():
        old.destroy()

    win = tk.Toplevel(app.root)
    app._mms_window = win  # 持有引用，防止被垃圾回收导致窗口不显示
    win.title(f"小地图合成 - {app.map_name_var.get().strip()}")
    win.transient(app.root)
    win.grab_set()
    win.resizable(False, False)

    def _on_close() -> None:
        app._mms_window = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _on_close)

    # 状态
    tk.Label(win, text="在游戏里移动角色，多次点击「截图」采集局部小地图，",
             font=("Microsoft YaHei", 10)).pack(pady=(12, 0))
    tk.Label(win, text="然后点「合成」查看结果，满意后点「保存」。",
             font=("Microsoft YaHei", 10)).pack(pady=(0, 6))
    status_var = tk.StringVar(value="")
    lbl_status = tk.Label(win, textvariable=status_var,
                          font=("Microsoft YaHei", 9), fg="#555")
    lbl_status.pack(pady=(0, 6))

    # 按钮行
    f_btns = tk.Frame(win)
    f_btns.pack(pady=4)

    shot_dir = _shots_dir(app)

    def _do_capture() -> None:
        """按小地图位置截图保存。"""
        frame = capture_client(app.target_hwnd)
        if frame is None:
            status_var.set("截图失败（窗口不可用）")
            return
        mm_img = frame[mt:mb, ml:mr]
        if mm_img.size == 0:
            status_var.set("小地图区域无效")
            return
        fname = shot_dir / f"mm_{time.strftime('%H%M%S')}_{len(list(shot_dir.glob('mm_*.png'))):04d}.png"
        ok, buf = cv2.imencode(".png", mm_img)
        if ok:
            fname.write_bytes(buf.tobytes())
        n = len(list(shot_dir.glob("mm_*.png")))
        status_var.set(f"已截图 {n} 张（{mm_img.shape[1]}x{mm_img.shape[0]}），继续走动再截图")

    def _do_stitch() -> None:
        """合成所有截图并显示。"""
        files = sorted(shot_dir.glob("mm_*.png"))
        if not files:
            status_var.set("还没有截图，请先点「截图」")
            return
        frames = []
        for f in files:
            data = np.fromfile(str(f), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is not None:
                frames.append(img)
        status_var.set(f"正在合成 {len(frames)} 张...")
        win.update()
        result = stitch_minimaps(frames, quiet=False)
        if result is None:
            status_var.set("合成失败：截图间无重叠，请放慢移动速度")
            return
        h, w = result.shape[:2]
        # 显示（宽度上限 760）
        scale = min(1.0, 760 / w)
        disp = Image.fromarray(result[:, :, ::-1]).resize(
            (int(w * scale), int(h * scale)), Image.LANCZOS)
        photo = ImageTk.PhotoImage(disp)
        canvas.delete("all")
        canvas.config(width=int(w * scale), height=int(h * scale))
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.photo = photo
        btn_save.config(state="normal")
        status_var.set(f"合成完成 {w}x{h}，检查是否完整，点「保存」")

    def _do_save() -> None:
        """保存合成图为该地图的完整小地图。"""
        files = sorted(shot_dir.glob("mm_*.png"))
        frames = []
        for f in files:
            data = np.fromfile(str(f), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is not None:
                frames.append(img)
        result = stitch_minimaps(frames)
        if result is None:
            status_var.set("合成失败，请先点「合成」确认")
            return
        out = _full_map_path(app)
        Image.fromarray(result[:, :, ::-1]).save(out)
        # 写元数据：标记"内容区"新格式及裁边量（供标记工具/加载识别）
        meta = {"trim": MINIMAP_TRIM,
                "content_size": [result.shape[1], result.shape[0]],
                "world_size": [result.shape[1] + 2 * MINIMAP_TRIM,
                               result.shape[0] + 2 * MINIMAP_TRIM]}
        out.with_name(out.stem + "_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        # 重新合成保存成功 → 旧图失效标志清除，可重新加载使用
        if hasattr(app, "_full_minimap_stale"):
            app._full_minimap_stale = False
        status_var.set(f"已保存完整小地图 → {out.name} ({result.shape[1]}x{result.shape[0]})")
        app.status_text.set(f"完整小地图已保存，运行 maps同步.bat 同步到自动脚本")

    btn_shot = tk.Button(f_btns, text="截图", font=("Microsoft YaHei", 11, "bold"),
                         width=10, bg="#3498db", fg="white", relief="flat",
                         cursor="hand2", command=_do_capture)
    btn_shot.pack(side="left", padx=4)
    btn_stitch = tk.Button(f_btns, text="合成", font=("Microsoft YaHei", 11, "bold"),
                           width=10, bg="#27ae60", fg="white", relief="flat",
                           cursor="hand2", command=_do_stitch)
    btn_stitch.pack(side="left", padx=4)
    btn_save = tk.Button(f_btns, text="保存", font=("Microsoft YaHei", 11, "bold"),
                         width=10, bg="#e67e22", fg="white", relief="flat",
                         state="disabled", cursor="hand2", command=_do_save)
    btn_save.pack(side="left", padx=4)

    # 结果显示区
    canvas = tk.Canvas(win, width=400, height=200, bg="#2d2d2d",
                       highlightthickness=0)
    canvas.pack(padx=8, pady=8)

    n_existing = len(list(shot_dir.glob("mm_*.png")))
    if n_existing:
        status_var.set(f"已有 {n_existing} 张截图，可直接「合成」或继续「截图」")
    else:
        status_var.set("请先在游戏里走动，然后点「截图」")
