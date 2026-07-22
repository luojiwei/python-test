"""地图预览与标记绘制函数集合。

所有函数都是纯函数 — 接收数据参数，返回 PIL Image。
不依赖 tkinter 或 MapMarkerApp 实例。
"""

import math

from PIL import Image, ImageDraw, ImageFont

try:
    from .rdp_simplify import rdp_simplify
except ImportError:
    from rdp_simplify import rdp_simplify  # type: ignore[no-redef]

# ==================== 平台审阅预览 ====================

PLATFORM_RDP_EPSILON = 2.5


def draw_platform_preview(mm_snapshot, mm_size, map_name, positions,
                          target_size=None, active_set=None):
    """绘制带玩家位置点和折线的平台审阅预览图。

    Args:
        mm_snapshot: PIL Image — 小地图背景截图 (可为 None)
        mm_size: (w, h) 小地图原始尺寸
        map_name: str
        positions: list[(x, y)] 所有记录位置
        target_size: (cw, ch) 画布尺寸 (可选)
        active_set: set((x,y)) 活跃位置集合 — 绿色标注；其他红色

    Returns:
        PIL Image
    """
    w, h = mm_size
    if w <= 0 or h <= 0:
        w, h = 154, 156

    if target_size is not None:
        canvas_w, canvas_h = target_size
    else:
        canvas_w, canvas_h = w, h
    ratio_x = canvas_w / w
    ratio_y = canvas_h / h
    ratio = (ratio_x + ratio_y) / 2

    if mm_snapshot is not None and mm_snapshot.size == (w, h):
        bg = mm_snapshot.resize((canvas_w, canvas_h), Image.LANCZOS)
        img = bg.copy()
        overlay = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 40))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img = img_rgba.convert("RGB")
    else:
        img = Image.new("RGB", (canvas_w, canvas_h), color=(245, 245, 240))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, canvas_w - 1, canvas_h - 1],
                   outline=(180, 180, 170), width=max(1, int(ratio)))

    try:
        font_path = "C:/Windows/Fonts/simhei.ttf"
        font = ImageFont.truetype(font_path, max(6, int(8 * ratio)))
    except Exception:
        font = ImageFont.load_default()

    GREEN = (46, 204, 113)
    RED = (231, 76, 60)
    active = active_set or set()

    # Draw dots: green if active, red if not
    for px, py in positions:
        dx = int(px * ratio_x)
        dy = int(py * ratio_y)
        color = GREEN if (px, py) in active else RED
        draw.ellipse([dx - 2, dy - 2, dx + 2, dy + 2], fill=color)

    # Only use active positions for polyline
    if active:
        active_list = sorted(active, key=lambda p: p[0])
        if len(active_list) >= 2:
            simplified = rdp_simplify(active_list, epsilon=PLATFORM_RDP_EPSILON)
            pts = [(int(p[0] * ratio_x), int(p[1] * ratio_y)) for p in simplified]
            for i in range(len(pts) - 1):
                draw.line([pts[i], pts[i+1]], fill=RED, width=2)
            for px, py in pts:
                draw.ellipse([px - 4, py - 4, px + 4, py + 4],
                             outline=RED, width=max(1, int(ratio)))

    active_count = len(active)
    total = len(positions)
    title = f"{map_name} - {active_count}活跃 {total}总计"
    draw.text((int(6 * ratio), int(3 * ratio)), title, fill=(60, 60, 60), font=font)
    return img


# ==================== 跳跃点审阅预览 ====================

def _draw_arrow(draw, fx: int, fy: int, tx: int, ty: int,
                rx: float, ry: float, color, wd: int = 2) -> None:
    """Draw an arrow from (fx,fy) to (tx,ty)."""
    dx1: int = int(fx * rx)
    dy1: int = int(fy * ry)
    dx2: int = int(tx * rx)
    dy2: int = int(ty * ry)

    # Shaft
    draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=wd)

    if dx1 == dx2 and dy1 == dy2:
        return

    angle = math.atan2(dy2 - dy1, dx2 - dx1)
    head_len: float = 5.0
    head_half: float = 2.5
    a1 = angle + math.radians(150)
    a2 = angle - math.radians(150)
    hx1 = int(dx2 + head_len * math.cos(a1))
    hy1 = int(dy2 + head_len * math.sin(a1))
    hx2 = int(dx2 + head_len * math.cos(a2))
    hy2 = int(dy2 + head_len * math.sin(a2))
    # Filled triangle
    draw.polygon([(dx2, dy2), (hx1, hy1), (hx2, hy2)], fill=color)
    mid_back = ((hx1 + hx2) // 2, (hy1 + hy2) // 2)
    draw.line([(hx1, hy1), (hx2, hy2)], fill=color, width=max(wd, 3))


def draw_jump_preview(mm_snapshot, mm_size, map_name,
                      new_jumps: list, old_jumps: list,
                      selected_source, selected_idx: int,
                      target_size=None):
    """Draw minimap with jump arrows (from → to).

    Colors:
      - New jumps:    cyan arrow
      - Old jumps:    blue arrow
      - Selected:     red arrow (thicker)
    """
    w, h = mm_size
    if w <= 0 or h <= 0:
        w, h = 154, 156

    if target_size is not None:
        cw, ch = target_size
    else:
        cw, ch = w, h
    rx: float = cw / w
    ry: float = ch / h
    ratio: float = (rx + ry) / 2

    if mm_snapshot is not None and mm_snapshot.size == (w, h):
        bg = mm_snapshot.resize((cw, ch), Image.LANCZOS)
        img = bg.copy()
        overlay = Image.new("RGBA", (cw, ch), (255, 255, 255, 50))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img = img_rgba.convert("RGB")
    else:
        img = Image.new("RGB", (cw, ch), color=(245, 245, 240))

    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, cw - 1, ch - 1],
                   outline=(180, 180, 170), width=max(1, int(ratio)))

    try:
        font_path = "C:/Windows/Fonts/simhei.ttf"
        font = ImageFont.truetype(font_path, max(6, int(8 * ratio)))
    except Exception:
        font = ImageFont.load_default()

    CYAN: tuple = (26, 188, 156)
    BLUE: tuple = (52, 152, 219)
    RED: tuple = (231, 76, 60)

    for i, r in enumerate(new_jumps):
        fx, fy, tx, ty = r[0], r[1], r[2], r[3]
        is_sel = (selected_source == "new" and selected_idx == i)
        color = RED if is_sel else CYAN
        _draw_arrow(draw, fx, fy, tx, ty, rx, ry, color, wd=4 if is_sel else 2)

    for i, r in enumerate(old_jumps):
        frm = r["from"]
        to = r["to"]
        is_sel = (selected_source == "old" and selected_idx == i)
        color = RED if is_sel else BLUE
        _draw_arrow(draw, frm["x"], frm["y"], to["x"], to["y"], rx, ry,
                    color, wd=4 if is_sel else 2)

    new_count = len(new_jumps)
    old_count = len(old_jumps)
    title = f"{map_name} - 新{new_count}个 旧{old_count}个"
    draw.text((int(6 * ratio), int(3 * ratio)), title, fill=(60, 60, 60), font=font)
    return img


# ==================== 闪现点审阅预览 ====================

def draw_flash_preview(mm_snapshot, mm_size, map_name,
                       new_flash: list, old_flash: list,
                       selected_source, selected_idx: int,
                       target_size=None):
    """Draw minimap with flash arrows (from → to).

    Colors:
      - One-way:  red
      - Two-way:  green
      - Selected: bright red
    """
    w, h = mm_size; w = w or 154; h = h or 156
    cw, ch = target_size or (w, h)
    rx, ry = cw / w, ch / h; ratio = (rx + ry) / 2
    if mm_snapshot and mm_snapshot.size == (w, h):
        img = mm_snapshot.resize((cw, ch), Image.LANCZOS).copy()
        ov = Image.new("RGBA", (cw, ch), (255, 255, 255, 50))
        img = img.convert("RGBA"); img.alpha_composite(ov); img = img.convert("RGB")
    else:
        img = Image.new("RGB", (cw, ch), color=(245, 245, 240))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, cw - 1, ch - 1], outline=(180, 180, 170), width=max(1, int(ratio)))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", max(6, int(8 * ratio)))
    except Exception:
        font = ImageFont.load_default()
    FL_COLOR: tuple = (46, 204, 113)  # green for flash preview
    FL_1W: tuple = (231, 76, 60)      # red = one_way
    FL_2W: tuple = (46, 204, 113)     # green = two_way

    def _draw_flash_arrow(fx, fy, tx, ty, color):
        dx1, dy1 = int(fx * rx), int(fy * ry)
        dx2, dy2 = int(tx * rx), int(ty * ry)
        draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=2)
        if dx1 != dx2 or dy1 != dy2:
            ang = math.atan2(dy2 - dy1, dx2 - dx1)
            hl = 5.0; a1 = ang + math.radians(150); a2 = ang - math.radians(150)
            draw.polygon([(dx2, dy2),
                (int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))),
                (int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2)))], fill=color)
            draw.line([(int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))),
                       (int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2)))], fill=color, width=3)

    for i, r in enumerate(new_flash):
        c = FL_COLOR
        if selected_source == "new" and selected_idx == i:
            c = (255, 0, 0)
        _draw_flash_arrow(r[0], r[1], r[2], r[3], c)

    for i, r in enumerate(old_flash):
        tp = r.get("type", "one_way")
        c = FL_2W if tp == "two_way" else FL_1W
        if selected_source == "old" and selected_idx == i:
            c = (255, 0, 0)
        frm, to = r["from"], r["to"]
        _draw_flash_arrow(frm["x"], frm["y"], to["x"], to["y"], c)

    draw.text((int(6 * ratio), int(3 * ratio)),
              f"{map_name} - 新{len(new_flash)}个 旧{len(old_flash)}个",
              fill=(60, 60, 60), font=font)
    return img


# ==================== 绳梯审阅预览 ====================

def draw_rope_preview(mm_snapshot, mm_size, map_name,
                      new_ropes: list, old_ropes: list,
                      selected_source, selected_idx: int,
                      target_size=None):
    """Draw minimap with rope ladder lines.

    Colors:
      - New ropes  (just recorded)   → green
      - Old ropes  (from maps.json)  → yellow
      - Selected rope                → red   (thicker)
    """
    w, h = mm_size
    if w <= 0 or h <= 0:
        w, h = 154, 156

    if target_size is not None:
        canvas_w, canvas_h = target_size
    else:
        canvas_w, canvas_h = w, h
    ratio_x: float = canvas_w / w
    ratio_y: float = canvas_h / h
    ratio: float = (ratio_x + ratio_y) / 2

    if mm_snapshot is not None and mm_snapshot.size == (w, h):
        bg = mm_snapshot.resize((canvas_w, canvas_h), Image.LANCZOS)
        img = bg.copy()
        overlay = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 60))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img = img_rgba.convert("RGB")
    else:
        img = Image.new("RGB", (canvas_w, canvas_h), color=(245, 245, 240))

    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, canvas_w - 1, canvas_h - 1],
                   outline=(180, 180, 170), width=max(1, int(ratio)))

    try:
        font_path = "C:/Windows/Fonts/simhei.ttf"
        font = ImageFont.truetype(font_path, max(6, int(8 * ratio)))
    except Exception:
        font = ImageFont.load_default()

    GREEN: tuple = (46, 204, 113)
    YELLOW: tuple = (241, 196, 15)
    RED: tuple = (231, 76, 60)

    def _rope_line(tx: int, ty: int, bx: int, by: int,
                   color: tuple, wd: int = 2) -> None:
        dx1: int = int(tx * ratio_x)
        dy1: int = int(ty * ratio_y)
        dx2: int = int(bx * ratio_x)
        dy2: int = int(by * ratio_y)
        draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=wd)
        r: int = 3 if wd <= 2 else 4
        draw.ellipse([dx1 - r, dy1 - r, dx1 + r, dy1 + r], fill=color)
        draw.ellipse([dx2 - r, dy2 - r, dx2 + r, dy2 + r], fill=color)

    # Draw new ropes
    for i, r in enumerate(new_ropes):
        tx, ty, bx, by = r
        is_sel: bool = (selected_source == "new" and selected_idx == i)
        color: tuple = RED if is_sel else GREEN
        _rope_line(tx, ty, bx, by, color, wd=4 if is_sel else 2)

    # Draw old ropes
    for i, r in enumerate(old_ropes):
        t: dict = r["top"]
        b: dict = r["bottom"]
        is_sel: bool = (selected_source == "old" and selected_idx == i)
        color: tuple = RED if is_sel else YELLOW
        _rope_line(t["x"], t["y"], b["x"], b["y"], color, wd=4 if is_sel else 2)

    new_count: int = len(new_ropes)
    old_count: int = len(old_ropes)
    title: str = f"{map_name} - 新{new_count}条 旧{old_count}条"
    draw.text((int(6 * ratio), int(3 * ratio)), title, fill=(60, 60, 60), font=font)
    return img


# ==================== 标记总览 ====================

def draw_markers_overview(mm_snapshot, mm_size, map_name,
                          platforms: list, ropes: list,
                          jumps: list, flashes: list,
                          target_size=None):
    """Draw minimap background with all saved platforms, rope ladders, jumps and flashes.

    Platforms are drawn with dots + simplified polyline in a single color.
    Rope ladders are drawn as yellow line segments with endpoint dots.
    Jumps are blue arrows; flashes are red/green arrows.
    """
    w, h = mm_size
    if w <= 0 or h <= 0:
        w, h = 154, 156

    if target_size is not None:
        cw, ch = target_size
    else:
        cw, ch = w, h
    rx: float = cw / w
    ry: float = ch / h
    ratio: float = (rx + ry) / 2

    if (mm_snapshot is not None and mm_snapshot.size == (w, h)):
        bg = mm_snapshot.resize((cw, ch), Image.LANCZOS)
        img = bg.copy()
        overlay = Image.new("RGBA", (cw, ch), (255, 255, 255, 50))
        img_rgba = img.convert("RGBA")
        img_rgba.alpha_composite(overlay)
        img = img_rgba.convert("RGB")
    else:
        img = Image.new("RGB", (cw, ch), color=(245, 245, 240))

    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, cw - 1, ch - 1],
                   outline=(180, 180, 170), width=max(1, int(ratio)))

    try:
        font_path = "C:/Windows/Fonts/simhei.ttf"
        font = ImageFont.truetype(font_path, max(6, int(8 * ratio)))
    except Exception:
        font = ImageFont.load_default()

    PLAT_COLOR = (155, 89, 182)
    ROPE_COLOR = (241, 196, 15)

    # --- Draw platforms ---
    for plat in platforms:
        all_pts = plat.get("all_points", [])
        if not all_pts:
            continue

        for px, py in all_pts:
            dx: int = int(px * rx)
            dy: int = int(py * ry)
            draw.ellipse([dx - 2, dy - 2, dx + 2, dy + 2], fill=PLAT_COLOR)

        tp = plat.get("turning_points", [])
        if len(tp) >= 2:
            pts = [(int(p["x"] * rx), int(p["y"] * ry)) for p in tp]
            for i in range(len(pts) - 1):
                draw.line([pts[i], pts[i + 1]], fill=PLAT_COLOR, width=2)
            for px, py in pts:
                draw.ellipse([px - 4, py - 4, px + 4, py + 4],
                             outline=PLAT_COLOR, width=max(1, int(ratio)))

    # --- Draw rope ladders ---
    for r in ropes:
        t: dict = r.get("top", {})
        b: dict = r.get("bottom", {})
        dx1: int = int(t.get("x", 0) * rx)
        dy1: int = int(t.get("y", 0) * ry)
        dx2: int = int(b.get("x", 0) * rx)
        dy2: int = int(b.get("y", 0) * ry)
        draw.line([(dx1, dy1), (dx2, dy2)], fill=ROPE_COLOR, width=2)
        draw.ellipse([dx1 - 3, dy1 - 3, dx1 + 3, dy1 + 3], fill=ROPE_COLOR)
        draw.ellipse([dx2 - 3, dy2 - 3, dx2 + 3, dy2 + 3], fill=ROPE_COLOR)

    # --- Draw jump arrows ---
    JUMP_COLOR: tuple = (52, 152, 219)
    for j in jumps:
        frm = j.get("from", {}); to = j.get("to", {})
        fx, fy = frm.get("x", 0), frm.get("y", 0)
        tx, ty = to.get("x", 0), to.get("y", 0)
        _draw_arrow(draw, fx, fy, tx, ty, rx, ry, JUMP_COLOR, wd=2)

    # --- Draw flash / teleport arrows ---
    FLASH_1WAY: tuple = (231, 76, 60)
    FLASH_2WAY: tuple = (46, 204, 113)
    for fl in flashes:
        frm = fl.get("from", {}); to = fl.get("to", {})
        fx, fy = frm.get("x", 0), frm.get("y", 0)
        tx, ty = to.get("x", 0), to.get("y", 0)
        tp = fl.get("type", "one_way")
        color = FLASH_2WAY if tp == "two_way" else FLASH_1WAY
        dx1, dy1 = int(fx * rx), int(fy * ry)
        dx2, dy2 = int(tx * rx), int(ty * ry)
        draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=2)
        if dx1 != dx2 or dy1 != dy2:
            ang = math.atan2(dy2 - dy1, dx2 - dx1)
            hl = 5.0; a1 = ang + math.radians(150); a2 = ang - math.radians(150)
            hx1, hy1 = int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))
            hx2, hy2 = int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2))
            draw.polygon([(dx2, dy2), (hx1, hy1), (hx2, hy2)], fill=color)
            draw.line([(hx1, hy1), (hx2, hy2)], fill=color, width=3)
            if tp == "two_way":
                ang2 = math.atan2(dy1 - dy2, dx1 - dx2)
                hx3, hy3 = int(dx1 + hl * math.cos(ang2 + math.radians(150))), int(dy1 + hl * math.sin(ang2 + math.radians(150)))
                hx4, hy4 = int(dx1 + hl * math.cos(ang2 - math.radians(150))), int(dy1 + hl * math.sin(ang2 - math.radians(150)))
                draw.polygon([(dx1, dy1), (hx3, hy3), (hx4, hy4)], fill=color)
                draw.line([(hx3, hy3), (hx4, hy4)], fill=color, width=3)

    # Title
    pcount: int = len(platforms)
    rcount: int = len(ropes)
    jcount: int = len(jumps)
    fcount: int = len(flashes)
    title: str = f"{map_name} - {pcount}平台 {rcount}绳梯 {jcount}跳跃 {fcount}闪现"
    draw.text((int(6 * ratio), int(3 * ratio)), title, fill=(60, 60, 60), font=font)
    return img
