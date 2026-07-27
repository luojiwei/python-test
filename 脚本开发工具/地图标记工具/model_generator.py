"""标记工具 — 世界模型生成器。

根据已标记的平台/绳梯/跳跃/闪现点生成世界模型。"""

import json, os, tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from .config import MAPS_FILE, OUTPUT_DIR
except ImportError:
    from config import MAPS_FILE, OUTPUT_DIR  # type: ignore[no-redef]


def open_model_generator(app) -> None:
    app._ensure_mm_snapshot()
    if app.running: app.status_text.set("标记运行中，请先停止"); return
    map_name: str = app.map_name_var.get().strip()
    if not map_name: app.status_text.set("请先输入地图名称"); return
    if not MAPS_FILE.exists(): app.status_text.set("maps.json 不存在"); return
    with open(MAPS_FILE, "r", encoding="utf-8") as f: data: dict = json.load(f)
    map_cfg: dict = data.get(map_name, {})
    platforms_raw: list = map_cfg.get("platforms", [])
    ropes: list = map_cfg.get("ropes", [])
    jumps: list = map_cfg.get("jumps", [])
    flashes: list = map_cfg.get("flash_points", [])
    if not platforms_raw: app.status_text.set("请先标记平台"); return
    platforms = []
    for i, p in enumerate(platforms_raw):
        np = dict(p); np["_idx"] = i; platforms.append(np)
    platforms.sort(key=lambda p: p["avg_y"], reverse=True)
    for i, p in enumerate(platforms): p["id"] = f"platform_{i}"

    def _find_platform(px, py, exclude=None):
        best_id, best = None, float("inf")
        for p in platforms:
            if exclude and p["id"] == exclude: continue
            if not (p["left_endpoint"]["x"] - 6 <= px <= p["right_endpoint"]["x"] + 6): continue
            py_min, py_max = p["min_y"] - 4, p["max_y"] + 4
            if not (py_min <= py <= py_max): continue
            dist = abs(py - p["avg_y"])
            if dist < best: best, best_id = dist, p["id"]
        return best_id

    edges: list[dict] = []; eid = 0

    def _add(typ, src, dst, **kw):
        nonlocal eid; eid += 1
        edges.append({"id": f"e{eid}", "type": typ, "from_platform": src, "to_platform": dst, **kw})

    for r in ropes:
        tx, ty = r["top"]["x"], r["top"]["y"]; bx, by = r["bottom"]["x"], r["bottom"]["y"]
        pt, pb = _find_platform(tx, ty), _find_platform(bx, by)
        if pt and pb and pt != pb:
            _add("rope", pb, pt, direction="up",
                 top={"x": tx, "y": ty}, bottom={"x": bx, "y": by})
            _add("rope", pt, pb, direction="down",
                 top={"x": tx, "y": ty}, bottom={"x": bx, "y": by})
    for j in jumps:
        ff, ft = j["from"], j["to"]; pf = _find_platform(ff["x"], ff["y"]); pt2 = _find_platform(ft["x"], ft["y"])
        if pf and pt2 and pf == pt2: pt2 = _find_platform(ft["x"], ft["y"], exclude=pf)
        if pf and pt2 and pf != pt2:
            _add("jump", pf, pt2, from_pt=ff, to_pt=ft); _add("jump", pt2, pf, from_pt=ft, to_pt=ff)
    for fl in flashes:
        ff, ft = fl["from"], fl["to"]; pf = _find_platform(ff["x"], ff["y"]); pt2 = _find_platform(ft["x"], ft["y"])
        ftp = fl.get("type", "one_way")
        if pf and pt2 and pf == pt2: pt2 = _find_platform(ft["x"], ft["y"], exclude=pf)
        if pf and pt2 and pf != pt2:
            _add("flash", pf, pt2, flash_type=ftp, from_pt=ff, to_pt=ft)
            if ftp == "two_way": _add("flash", pt2, pf, flash_type=ftp, from_pt=ft, to_pt=ff)
    edir: list[dict] = list(edges)

    sw, sh = app.mm_size
    scale: float = min(6.0, 700 / max(sw, sh, 1))
    dw: int = int(sw * scale); dh: int = int(sh * scale)

    mgr = tk.Toplevel(app.root)
    mgr.title(f"地图模型 - {map_name}")
    mgr.transient(app.root); mgr.grab_set()
    left = tk.Frame(mgr); left.pack(side="left", padx=10, pady=10)
    canvas = tk.Canvas(left, width=dw, height=dh, highlightthickness=0); canvas.pack()
    sel_idx = [-1]
    PLAT_COLOR = (80, 200, 255, 120); PLAT_HI = (255, 220, 80, 200)
    EDGE_COLORS = {"rope": (255, 200, 40), "jump": (180, 80, 220), "flash": (255, 100, 30)}
    HI_COLOR = (255, 50, 50)

    def _draw_map() -> ImageTk.PhotoImage:
        w2, h2 = sw, sh; rx, ry = dw / w2, dh / h2; rt = (rx + ry) / 2
        if app._mm_snapshot and app._mm_snapshot.size == (w2, h2):
            img = app._mm_snapshot.resize((dw, dh), Image.LANCZOS).copy()
        else: img = Image.new("RGB", (dw, dh), (245, 245, 240))
        draw = ImageDraw.Draw(img)
        try: fnt = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", max(6, int(8 * rt)))
        except Exception: fnt = ImageFont.load_default()
        si = sel_idx[0]
        sel_e = edir[si] if 0 <= si < len(edir) else None
        sel_from, sel_to = (sel_e["from_platform"], sel_e["to_platform"]) if sel_e else (None, None)
        for p in platforms:
            pts = p.get("all_points", [])
            if not pts: continue
            poly = [(int(x * rx), int(y * ry)) for x, y in pts]
            hi = (p["id"] in (sel_from, sel_to))
            draw.polygon(poly, fill=PLAT_HI if hi else PLAT_COLOR, outline=(255, 255, 255, 230))
            cx = sum(x for x, _ in poly) // len(poly); cy = sum(y for _, y in poly) // len(poly)
            lbl = p["id"].replace("platform_", "P")
            tc = (255, 200, 50) if hi else (255, 255, 255)
            draw.text((cx - 10, cy - 8), lbl, fill=tc, font=fnt)
        for i, e in enumerate(edir):
            if e["type"] == "rope":
                tp = e.get("top", {}); bp = e.get("bottom", {})
                x1, y1 = int(bp.get("x", 0) * rx), int(bp.get("y", 0) * ry)
                x2, y2 = int(tp.get("x", 0) * rx), int(tp.get("y", 0) * ry)
            else:
                fp, tp = e.get("from_pt", {}), e.get("to_pt", {})
                x1, y1 = int(fp.get("x", 0) * rx), int(fp.get("y", 0) * ry)
                x2, y2 = int(tp.get("x", 0) * rx), int(tp.get("y", 0) * ry)
            is_sel = (i == si)
            c = HI_COLOR if is_sel else EDGE_COLORS.get(e["type"], (200, 200, 200))
            w = 4 if is_sel else 2
            draw.line([(x1, y1), (x2, y2)], fill=c, width=w)
            r = 6 if is_sel else 4
            draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=c)
            draw.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=c)
        return ImageTk.PhotoImage(img)

    canvas.photo = _draw_map()
    canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

    def _refresh() -> None:
        canvas.photo = _draw_map()
        canvas.delete("all"); canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

    right = tk.Frame(mgr); right.pack(side="right", fill="y", padx=10, pady=10)
    tk.Label(right, text=f"关联关系: {len(edir)} 条",
             font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
    lf = tk.Frame(right); lf.pack(fill="both", expand=True)
    lb = tk.Listbox(lf, font=("Microsoft YaHei", 10), width=30, height=22,
                    selectmode="single", exportselection=False)
    lb.pack(side="left", fill="both", expand=True)
    sb2 = tk.Scrollbar(lf, orient="vertical", command=lb.yview)
    sb2.pack(side="right", fill="y"); lb.config(yscrollcommand=sb2.set)
    TYPE_CN = {"rope": "绳梯", "jump": "跳跃", "flash": "闪现"}

    def _fill() -> None:
        lb.delete(0, "end")
        for i, e in enumerate(edir):
            tc = TYPE_CN.get(e["type"], e["type"])
            src = e["from_platform"].replace("platform_", "P")
            dst = e["to_platform"].replace("platform_", "P")
            label = f"{tc}: {src} → {dst}"
            if e["type"] == "rope": label += f" [{e.get('direction','?')}]"
            elif e["type"] == "flash":
                ft = e.get("flash_type", "one_way")
                label += " [单向]" if ft == "one_way" else " [双向]"
            lb.insert("end", label)
    _fill()

    def _on_select(evt) -> None:
        s = lb.curselection(); sel_idx[0] = s[0] if s else -1; _refresh()
    lb.bind("<<ListboxSelect>>", _on_select)

    bbar = tk.Frame(right); bbar.pack(fill="x", pady=(8, 0))

    def _del() -> None:
        s = lb.curselection()
        if not s: return
        del edir[s[0]]; sel_idx[0] = -1; _fill(); _refresh()

    def _edit() -> None:
        s = lb.curselection()
        if not s: return
        idx = s[0]; e = edir[idx]
        ew = tk.Toplevel(mgr); ew.title("编辑关联"); ew.transient(mgr); ew.grab_set()
        f = tk.Frame(ew, padx=12, pady=10); f.pack()
        plat_ids = [p["id"] for p in platforms]
        tk.Label(f, text="起点平台:", font=("Microsoft YaHei", 9)).grid(row=0, column=0, sticky="e")
        sv = tk.StringVar(value=e["from_platform"]); tk.OptionMenu(f, sv, *plat_ids).grid(row=0, column=1, padx=4)
        tk.Label(f, text="终点平台:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e")
        dv = tk.StringVar(value=e["to_platform"]); tk.OptionMenu(f, dv, *plat_ids).grid(row=1, column=1, padx=4)
        tk.Label(f, text="类型:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e")
        tv = tk.StringVar(value=e["type"]); tk.OptionMenu(f, tv, "rope", "jump", "flash").grid(row=2, column=1, padx=4)
        def _ap():
            e["from_platform"] = sv.get(); e["to_platform"] = dv.get(); e["type"] = tv.get()
            _fill(); _refresh(); ew.destroy()
        bf2 = tk.Frame(f); bf2.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        tk.Button(bf2, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_ap).pack(side="left", padx=4)
        tk.Button(bf2, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

    def _add() -> None:
        nonlocal eid
        ew = tk.Toplevel(mgr); ew.title("新增关联"); ew.transient(mgr); ew.grab_set()
        f = tk.Frame(ew, padx=12, pady=10); f.pack()
        plat_ids = [p["id"] for p in platforms]
        tk.Label(f, text="起点平台:", font=("Microsoft YaHei", 9)).grid(row=0, column=0, sticky="e")
        sv = tk.StringVar(value=plat_ids[0] if plat_ids else ""); tk.OptionMenu(f, sv, *plat_ids).grid(row=0, column=1, padx=4)
        tk.Label(f, text="终点平台:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e")
        dv = tk.StringVar(value=plat_ids[0] if plat_ids else ""); tk.OptionMenu(f, dv, *plat_ids).grid(row=1, column=1, padx=4)
        tk.Label(f, text="类型:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e")
        tv = tk.StringVar(value="jump"); tk.OptionMenu(f, tv, "rope", "jump", "flash").grid(row=2, column=1, padx=4)
        def _ap():
            nonlocal eid; eid += 1
            edir.append({"id": f"e{eid}", "type": tv.get(), "from_platform": sv.get(),
                         "to_platform": dv.get(), "from_pt": {"x": 0, "y": 0}, "to_pt": {"x": 0, "y": 0}})
            _fill(); _refresh(); ew.destroy()
        bf2 = tk.Frame(f); bf2.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        tk.Button(bf2, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_ap).pack(side="left", padx=4)
        tk.Button(bf2, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

    def _save() -> None:
        output = {"map_name": map_name, "minimap_size": list(app.mm_size),
                  "mm_region": list(app.mm_offsets), "platforms": platforms, "edges": edir}
        out_path = os.path.join(OUTPUT_DIR, f"{map_name}_model.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        app.status_text.set(f"模型已保存: {out_path}")
        mgr.destroy()

    tk.Button(bbar, text="编辑", font=("Microsoft YaHei", 9), width=6, bg="#3498db", fg="white", command=_edit).pack(side="left", padx=2)
    tk.Button(bbar, text="删除", font=("Microsoft YaHei", 9), width=6, bg="#e74c3c", fg="white", command=_del).pack(side="left", padx=2)
    tk.Button(bbar, text="新增", font=("Microsoft YaHei", 9), width=6, bg="#2ecc71", fg="white", command=_add).pack(side="left", padx=2)
    tk.Button(bbar, text="保存模型", font=("Microsoft YaHei", 9, "bold"), width=10, bg="#8e44ad", fg="white", command=_save).pack(side="left", padx=6)
    app.root.wait_window(mgr)
