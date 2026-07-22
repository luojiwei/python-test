"""标记工具 — 巡逻路线编辑器。

打开巡逻路线编辑对话框。"""

import json, re, tkinter as tk

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from .anchor_system import AnchorResolver
    from .config import MAPS_FILE
except ImportError:
    from anchor_system import AnchorResolver  # type: ignore[no-redef]
    from config import MAPS_FILE  # type: ignore[no-redef]


def open_patrol_route_editor(app) -> None:
    app._ensure_mm_snapshot()
    if app.running: app.status_text.set("标记运行中，请先停止"); return
    map_name: str = app.map_name_var.get().strip()
    if not map_name: app.status_text.set("请先输入地图名称"); return
    if not MAPS_FILE.exists(): app.status_text.set("maps.json 不存在，请先标记地图"); return
    with open(MAPS_FILE, "r", encoding="utf-8") as f: data: dict = json.load(f)
    map_cfg: dict = data.get(map_name, {})
    platforms_raw: list = map_cfg.get("platforms", [])
    ropes: list = map_cfg.get("ropes", [])
    jumps: list = map_cfg.get("jumps", [])
    flashes: list = map_cfg.get("flash_points", [])
    if not platforms_raw: app.status_text.set("请先生成世界模型（需先标记平台）"); return
    platforms: list[dict] = []
    for i, p in enumerate(platforms_raw):
        np = dict(p); np["_idx"] = i; platforms.append(np)
    platforms.sort(key=lambda p: p["avg_y"], reverse=True)
    for i, p in enumerate(platforms): p["id"] = f"platform_{i}"
    resolver = AnchorResolver(platforms, ropes, jumps, flashes)
    saved_routes: list = map_cfg.get("patrol_routes", [])
    if saved_routes:
        sr = saved_routes[0]
        saved_name: str = sr.get("route_name", "默认巡逻路线")
        saved_return: str = sr.get("return_method", "无")
        raw = sr.get("waypoints", sr.get("segments", []))
        if raw and isinstance(raw[0], dict):
            saved_waypoints: list[str] = []
            for i, s in enumerate(raw):
                if i == 0: saved_waypoints.append(s.get("start_anchor", s.get("start", "")))
                saved_waypoints.append(s.get("end_anchor", s.get("end", "")))
        else: saved_waypoints = raw
    else: saved_waypoints: list[str] = []; saved_name = "默认巡逻路线"; saved_return = "无"

    sw, sh = app.mm_size
    scale: float = min(5.0, 600 / max(sw, sh, 1))
    dw: int = int(sw * scale); dh: int = int(sh * scale)
    win = tk.Toplevel(app.root)
    win.title(f"巡逻路线编辑器 - {map_name}")
    win.transient(app.root); win.grab_set()
    left = tk.Frame(win); left.pack(side="left", padx=10, pady=10)
    canvas = tk.Canvas(left, width=dw, height=dh, highlightthickness=0); canvas.pack()
    legend = tk.Frame(left); legend.pack(pady=(6, 0))
    LEGEND_ITEMS = [
        ("● 平台端点", "#9b59b6"), ("━ 绳梯", "#f1c40f"),
        ("━ 跳跃点", "#3498db"), ("━ 闪现点", "#e74c3c"),
    ]
    for txt, clr in LEGEND_ITEMS:
        tk.Label(legend, text=txt, font=("Microsoft YaHei", 8), fg=clr).pack(side="left", padx=4)
    waypoints: list[str] = list(saved_waypoints)
    ROUTE_COLORS: list[tuple[int, int, int]] = [
        (220, 50, 50), (46, 134, 222), (39, 174, 96), (243, 156, 18),
        (155, 89, 182), (52, 73, 94), (22, 160, 133), (142, 68, 173)]
    PLAT_COLOR = (80, 200, 255, 120)
    ROPE_COLOR = (241, 196, 15); JUMP_COLOR = (52, 152, 219)
    FLASH_1_COLOR = (231, 76, 60); FLASH_2_COLOR = (46, 204, 113)

    def _resolve_connection_point(anchor, other_anchor) -> tuple[int, int]:
        try:
            from .anchor_system import AnchorPoint
        except ImportError:
            from anchor_system import AnchorPoint  # type: ignore[no-redef]
        if anchor.anchor_type in ("plat_left", "plat_right"):
            return (anchor.x, anchor.y)
        for pid in other_anchor.platform_ids:
            sp = resolver.get_sub_point_for_platform(anchor.anchor_id, pid)
            if sp: return (sp["x"], sp["y"])
        if anchor.sub_points: sp = anchor.sub_points[0]; return (sp["x"], sp["y"])
        return (anchor.x, anchor.y)

    def _draw_map() -> ImageTk.PhotoImage:
        w2, h2 = sw, sh; rx, ry = dw / max(w2, 1), dh / max(h2, 1); rt = (rx + ry) / 2
        if app._mm_snapshot and app._mm_snapshot.size == (w2, h2):
            img = app._mm_snapshot.resize((dw, dh), Image.LANCZOS).copy()
        else: img = Image.new("RGB", (dw, dh), (245, 245, 240))
        draw = ImageDraw.Draw(img)
        try:
            fnt = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", max(6, int(9 * rt)))
            fnt_sm = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", max(5, int(7 * rt)))
        except Exception: fnt = ImageFont.load_default(); fnt_sm = fnt
        for p in platforms:
            pts = p.get("all_points", [])
            if not pts: continue
            poly = [(int(x * rx), int(y * ry)) for x, y in pts]
            draw.polygon(poly, fill=PLAT_COLOR, outline=(255, 255, 255, 230))
            cx = sum(x for x, _ in poly) // max(len(poly), 1)
            cy = sum(y for _, y in poly) // max(len(poly), 1)
            lbl = p["id"].replace("platform_", "P")
            draw.text((cx - 10, cy - 8), lbl, fill=(255, 255, 255), font=fnt)
            le = p["left_endpoint"]; re = p["right_endpoint"]
            lx, ly = int(le["x"] * rx), int(le["y"] * ry)
            rx2, ry2 = int(re["x"] * rx), int(re["y"] * ry)
            r2 = max(1, int(2 * rt))
            draw.ellipse([lx - r2, ly - r2, lx + r2, ly + r2], fill="#9b59b6")
            draw.ellipse([rx2 - r2, ry2 - r2, rx2 + r2, ry2 + r2], fill="#9b59b6")
        for i, r in enumerate(ropes):
            tx, ty = int(r["top"]["x"] * rx), int(r["top"]["y"] * ry)
            bx, by = int(r["bottom"]["x"] * rx), int(r["bottom"]["y"] * ry)
            draw.line([(tx, ty), (bx, by)], fill=ROPE_COLOR, width=3)
            mx, my = (tx + bx) // 2, (ty + by) // 2
            draw.text((mx + 4, my - 6), f"R{i}", fill=ROPE_COLOR, font=fnt_sm)
        for i, j in enumerate(jumps):
            fx, fy = int(j["from"]["x"] * rx), int(j["from"]["y"] * ry)
            tx, ty = int(j["to"]["x"] * rx), int(j["to"]["y"] * ry)
            draw.line([(fx, fy), (tx, ty)], fill=JUMP_COLOR, width=3)
            mx, my = (fx + tx) // 2, (fy + ty) // 2
            draw.text((mx + 4, my - 6), f"J{i}", fill=JUMP_COLOR, font=fnt_sm)
        for i, fl in enumerate(flashes):
            fx, fy = int(fl["from"]["x"] * rx), int(fl["from"]["y"] * ry)
            tx, ty = int(fl["to"]["x"] * rx), int(fl["to"]["y"] * ry)
            ft = fl.get("type", "one_way")
            clr = FLASH_2_COLOR if ft == "two_way" else FLASH_1_COLOR
            draw.line([(fx, fy), (tx, ty)], fill=clr, width=3)
            mx, my = (fx + tx) // 2, (fy + ty) // 2
            draw.text((mx + 4, my - 6), f"F{i}", fill=clr, font=fnt_sm)
        if len(waypoints) >= 2:
            anchors = [resolver.get_by_id(wid) for wid in waypoints]
            for i in range(len(anchors) - 1):
                sa, ea = anchors[i], anchors[i + 1]
                if not sa or not ea: continue
                sx, sy = _resolve_connection_point(sa, ea)
                ex, ey = _resolve_connection_point(ea, sa)
                x1, y1 = int(sx * rx), int(sy * ry)
                x2, y2 = int(ex * rx), int(ey * ry)
                seg_color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
                draw.line([(x1, y1), (x2, y2)], fill=seg_color, width=3)
                mx2, my2 = (x1 + x2) // 2, (y1 + y2) // 2
                seg_len = max(1.0, ((x2 - x1)**2 + (y2 - y1)**2)**0.5)
                dx, dy = (x2 - x1) / seg_len, (y2 - y1) / seg_len
                arr_sz = max(3, int(4 * rt))
                draw.polygon(
                    [(int(mx2 + dx * arr_sz), int(my2 + dy * arr_sz)),
                     (int(mx2 - dx * arr_sz / 2 - dy * arr_sz / 2),
                      int(my2 - dy * arr_sz / 2 + dx * arr_sz / 2)),
                     (int(mx2 - dx * arr_sz / 2 + dy * arr_sz / 2),
                      int(my2 - dy * arr_sz / 2 - dx * arr_sz / 2))],
                    fill=seg_color)
        return ImageTk.PhotoImage(img)

    canvas.photo = _draw_map()
    canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

    def _refresh_map() -> None:
        canvas.photo = _draw_map()
        canvas.delete("all"); canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

    right = tk.Frame(win); right.pack(side="right", fill="y", padx=10, pady=10)
    tk.Label(right, text="路线编辑（链式途经点）",
             font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
    bbar_top = tk.Frame(right); bbar_top.pack(fill="x", pady=(0, 6))
    tk.Button(bbar_top, text="+ 添加途经点", font=("Microsoft YaHei", 9),
              width=14, bg="#2ecc71", fg="white", cursor="hand2",
              command=lambda: _add_waypoint()).pack(side="left", padx=2)
    tk.Button(bbar_top, text="清空所有", font=("Microsoft YaHei", 9),
              width=10, bg="#95a5a6", fg="white", cursor="hand2",
              command=lambda: _clear_all()).pack(side="left", padx=2)
    seg_frame = tk.Frame(right); seg_frame.pack(fill="both", expand=True)
    seg_canvas = tk.Canvas(seg_frame, width=380, highlightthickness=0)
    seg_scroll = tk.Scrollbar(seg_frame, orient="vertical", command=seg_canvas.yview)
    seg_inner = tk.Frame(seg_canvas)
    seg_inner.bind("<Configure>", lambda e: seg_canvas.configure(scrollregion=seg_canvas.bbox("all")))
    seg_canvas.create_window((0, 0), window=seg_inner, anchor="nw")
    seg_canvas.configure(yscrollcommand=seg_scroll.set)
    seg_canvas.pack(side="left", fill="both", expand=True)
    seg_scroll.pack(side="right", fill="y")
    empty_lbl = tk.Label(seg_inner, text="请添加途经点开始编辑\n（按顺序连接）",
                         font=("Microsoft YaHei", 9), fg="#999")
    empty_lbl.pack(pady=10)
    waypoint_rows: list[dict] = []
    full_options: list[tuple[str, str]] = resolver.grouped_options

    def _build_map_options(option_pairs):
        dm, dl = {}, []
        for aid, label in option_pairs:
            dm[label] = aid; dl.append(label)
        return dl, dm

    full_display_vals, full_display_to_id = _build_map_options(full_options)

    def _add_waypoint(wid: str = "") -> None:
        empty_lbl.pack_forget()
        idx = len(waypoint_rows)
        row = tk.Frame(seg_inner); row.pack(fill="x", pady=2)
        tk.Label(row, text=f"途经{idx + 1}:", font=("Microsoft YaHei", 9),
                 width=6, anchor="e").pack(side="left")
        var = tk.StringVar(value="")
        om = tk.OptionMenu(row, var, *full_display_vals)
        om.config(font=("Microsoft YaHei", 9), width=22, anchor="w")
        om.pack(side="left", padx=2)

        def _on_change(*_args): _sync_waypoints(); _refresh_map()
        var.trace_add("write", _on_change)
        btn_del = tk.Button(row, text="×", font=("Microsoft YaHei", 9, "bold"),
                            width=2, bg="#e74c3c", fg="white", relief="flat",
                            cursor="hand2",
                            command=lambda ridx=idx: _delete_waypoint(ridx))
        btn_del.pack(side="left", padx=4)
        waypoint_rows.append({"var": var, "frame": row, "idx": idx, "menu": om})
        if wid:
            for disp, aid in full_display_to_id.items():
                if aid == wid: var.set(disp); break
        _sync_waypoints(); _refresh_map()

    def _delete_waypoint(ridx: int) -> None:
        if ridx < len(waypoint_rows):
            waypoint_rows[ridx]["frame"].destroy()
            waypoint_rows.pop(ridx)
            for i, r in enumerate(waypoint_rows):
                for child in r["frame"].winfo_children():
                    if isinstance(child, tk.Label) and "途经" in (child.cget("text") or ""):
                        child.config(text=f"途经{i + 1}:"); break
                r["idx"] = i
        if not waypoint_rows: empty_lbl.pack(pady=10)
        _sync_waypoints(); _refresh_map()

    def _clear_all() -> None:
        for r in waypoint_rows: r["frame"].destroy()
        waypoint_rows.clear(); empty_lbl.pack(pady=10)
        _sync_waypoints(); _refresh_map()

    def _sync_waypoints() -> None:
        waypoints.clear()
        for r in waypoint_rows:
            aid = full_display_to_id.get(r["var"].get(), "")
            if aid: waypoints.append(aid)

    if saved_waypoints:
        empty_lbl.pack_forget()
        for wid in saved_waypoints: _add_waypoint(wid)

    name_frame = tk.Frame(right); name_frame.pack(fill="x", pady=(10, 6))
    tk.Label(name_frame, text="路线名称:", font=("Microsoft YaHei", 9),
             width=9, anchor="e").pack(side="left")
    name_var = tk.StringVar(value=saved_name)
    tk.Entry(name_frame, textvariable=name_var, font=("Microsoft YaHei", 10), width=28).pack(side="left", padx=(4, 0))

    return_frame = tk.Frame(right); return_frame.pack(fill="x", pady=(0, 6))
    tk.Label(return_frame, text="回归方式:", font=("Microsoft YaHei", 9),
             width=9, anchor="e").pack(side="left")
    return_method_var = tk.StringVar(value=saved_return)
    return_om = tk.OptionMenu(return_frame, return_method_var, "无", "一直走", "下跳")
    return_om.config(font=("Microsoft YaHei", 9), width=26, anchor="w")
    return_om.pack(side="left", padx=(4, 0))

    bbar_bot = tk.Frame(right); bbar_bot.pack(fill="x", pady=(6, 0))

    def _save() -> None:
        _sync_waypoints()
        with open(MAPS_FILE, "r", encoding="utf-8") as f: all_data: dict = json.load(f)
        route_data = {"route_name": name_var.get().strip() or "默认巡逻路线", "waypoints": list(waypoints),
                      "return_method": return_method_var.get()}
        all_data.setdefault(map_name, {})["patrol_routes"] = [route_data]
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        app.status_text.set(f"巡逻路线已保存到 {map_name}")
        win.destroy()

    tk.Button(bbar_bot, text="保存路线", font=("Microsoft YaHei", 10, "bold"),
              width=12, bg="#8e44ad", fg="white", command=_save, cursor="hand2").pack(side="left", padx=2)
    tk.Button(bbar_bot, text="取消", font=("Microsoft YaHei", 10),
              width=8, bg="#95a5a6", fg="white", command=win.destroy, cursor="hand2").pack(side="left", padx=2)
    app.root.wait_window(win)
