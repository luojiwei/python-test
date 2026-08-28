"""标记工具 — 查看与编辑标记。

左侧显示完整小地图上的所有标记（可高亮选中项），右侧列出
平台/绳梯/跳跃点/闪现点 分类列表，选中某项后显示其坐标点列表，
每个坐标点可编辑（弹窗输入新值）或删除，改动实时反映到地图并写回 maps.json。
"""

import json
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from PIL import Image, ImageTk

try:
    from .config import get_map_path
    from .drawing import draw_markers_overview
except ImportError:
    from config import get_map_path  # type: ignore[no-redef]
    from drawing import draw_markers_overview  # type: ignore[no-redef]

CATS = [("platform", "平台"), ("rope", "绳梯"), ("jump", "跳跃点"), ("flash", "闪现点")]


def _load_map_data(map_path) -> dict:
    """读取 maps.json；不存在返回空字典。"""
    if not map_path.exists():
        return {}
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def open_viewer(app) -> None:
    app._ensure_mm_snapshot()
    if app.running:
        app.status_text.set("标记运行中，请先停止")
        return
    map_name: str = app.map_name_var.get().strip()
    if not map_name:
        app.status_text.set("请先输入地图名称")
        return
    win_name: str = app._window_var.get().strip()
    if not win_name:
        app.status_text.set("请先选择游戏窗口")
        return
    map_path = get_map_path(win_name, map_name)
    map_cfg: dict = _load_map_data(map_path)
    platforms: list = map_cfg.get("platforms", [])
    ropes: list = map_cfg.get("ropes", [])
    jumps: list = map_cfg.get("jumps", [])
    if not jumps:
        jumps = map_cfg.get("teleports", [])
    flashes: list = map_cfg.get("flash_points", [])
    if not platforms and not ropes and not jumps and not flashes:
        app.status_text.set(f"地图 '{map_name}' 尚无标记数据")
        return

    mw, mh = app.mm_size
    if mw <= 0 or mh <= 0:
        mw, mh = 154, 156
    scale: float = min(6.0, 700 / max(mw, mh, 1))
    dw: int = int(mw * scale)
    dh: int = int(mh * scale)

    win = tk.Toplevel(app.root)
    app._viewer_window = win  # 持有引用防 GC
    win.title(f"查看/编辑标记 - {map_name}")
    win.transient(app.root)
    win.grab_set()
    win.geometry(f"{max(dw + 300, 820)}x{max(dh + 60, 620)}")

    def _on_close() -> None:
        win.destroy()
        app._viewer_window = None

    win.protocol("WM_DELETE_WINDOW", _on_close)

    # ---- 左侧：地图画布 ----
    left = tk.Frame(win)
    left.pack(side="left", padx=8, pady=8)
    canvas = tk.Canvas(left, width=dw, height=dh, highlightthickness=0, bg="#f5f5f0")
    canvas.pack()

    # ---- 右侧：分类 + 坐标点列表 ----
    right = tk.Frame(win)
    right.pack(side="right", fill="both", expand=True, padx=(0, 8), pady=8)

    tk.Label(right, text="标记列表（点击项 → 下方编辑坐标点）",
             font=("Microsoft YaHei", 9), fg="#555").pack(anchor="w")

    tree = ttk.Treeview(right, show="tree", height=10)
    tree.pack(fill="x", pady=(2, 6))
    tree.bind("<<TreeviewSelect>>", lambda e: _on_tree_select())

    tk.Label(right, text="坐标点列表：", font=("Microsoft YaHei", 9), fg="#555").pack(anchor="w")
    pt_list = tk.Listbox(right, font=("Courier", 10), height=12,
                         activestyle="dotbox")
    pt_list.pack(fill="both", expand=True, pady=(2, 6))

    btns = tk.Frame(right)
    btns.pack(fill="x")
    btn_edit = tk.Button(btns, text="编辑坐标", font=("Microsoft YaHei", 10, "bold"),
                         width=10, bg="#3498db", fg="white", relief="flat",
                         cursor="hand2", command=lambda: _on_edit())
    btn_edit.pack(side="left", padx=2)
    btn_del = tk.Button(btns, text="删除坐标", font=("Microsoft YaHei", 10, "bold"),
                        width=10, bg="#e74c3c", fg="white", relief="flat",
                        cursor="hand2", command=lambda: _on_delete())
    btn_del.pack(side="left", padx=2)
    hint = tk.Label(right, text="", font=("Microsoft YaHei", 9), fg="#888",
                    justify="left", anchor="w")
    hint.pack(fill="x", pady=(6, 0))

    # ---- 状态 ----
    state = {
        "cat": None,     # 当前选中分类（platform/rope/jump/flash）
        "idx": -1,       # 当前选中项索引
    }

    def _item_points(item: dict, cat: str) -> list:
        """返回该项的坐标点列表（引用，可直接修改）。"""
        if cat == "platform":
            tp = item.get("turning_points")
            if tp:
                return tp
            # 无关键点则从 all_points 构造
            pts = [{"x": float(x), "y": float(y)} for x, y in item.get("all_points", [])]
            item["turning_points"] = pts
            return pts
        if cat == "rope":
            return [item["top"], item["bottom"]]
        if cat == "jump":
            return [item["from"], item["to"]]
        if cat == "flash":
            return [item["from"], item["to"]]
        return []

    def _get_items(cat: str) -> list:
        return {"platform": platforms, "rope": ropes,
                "jump": jumps, "flash": flashes}[cat]

    def _sync_platform_derived(plat: dict) -> None:
        """平台关键点变化后同步端点与 y 统计。"""
        tp = plat.get("turning_points") or []
        if not tp:
            return
        plat["left_endpoint"] = {"x": tp[0]["x"], "y": tp[0]["y"]}
        plat["right_endpoint"] = {"x": tp[-1]["x"], "y": tp[-1]["y"]}
        ys = [p["y"] for p in tp]
        plat["min_y"] = min(ys)
        plat["max_y"] = max(ys)
        plat["avg_y"] = sum(ys) / len(ys)

    def _save() -> None:
        map_cfg["platforms"] = platforms
        map_cfg["ropes"] = ropes
        map_cfg["jumps"] = jumps
        map_cfg["flash_points"] = flashes
        map_path.parent.mkdir(parents=True, exist_ok=True)
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(map_cfg, f, ensure_ascii=False, indent=2)

    def _redraw() -> None:
        highlight = {}
        if state["cat"] is not None and state["idx"] >= 0:
            highlight[state["cat"]] = state["idx"]
        img = draw_markers_overview(
            app._mm_snapshot, (mw, mh), map_name,
            platforms, ropes, jumps, flashes,
            target_size=(dw, dh), highlight=highlight)
        photo = ImageTk.PhotoImage(img)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.photo = photo  # 持有引用

    def _rebuild_tree() -> None:
        tree.delete(*tree.get_children())
        for cat, cn in CATS:
            items = _get_items(cat)
            cid = tree.insert("", "end", iid=f"cat_{cat}",
                              text=f"{cn} ({len(items)})", open=True)
            for i, _it in enumerate(items):
                tree.insert(cid, "end", iid=f"{cat}_{i}",
                            text=f"{cn} {i + 1}")

    def _refresh_pt_list() -> None:
        pt_list.delete(0, "end")
        if state["cat"] is None or state["idx"] < 0:
            hint.config(text="请在左侧选择一个标记项")
            return
        items = _get_items(state["cat"])
        if state["idx"] >= len(items):
            return
        pts = _item_points(items[state["idx"]], state["cat"])
        for i, p in enumerate(pts):
            pt_list.insert("end", f"({i + 1}) x={p['x']:.0f}  y={p['y']:.0f}")
        hint.config(text=f"{dict(CATS)[state['cat']]} {state['idx'] + 1}："
                         f"{len(pts)} 个坐标点，双击行也可编辑")

    def _on_tree_select() -> None:
        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("cat_"):
            state["cat"] = None
            state["idx"] = -1
        else:
            cat, idx = iid.rsplit("_", 1)
            state["cat"] = cat
            state["idx"] = int(idx)
        _redraw()
        _refresh_pt_list()

    def _on_edit() -> None:
        if state["cat"] is None or state["idx"] < 0:
            hint.config(text="请先在左侧选择一个标记项")
            return
        sel = pt_list.curselection()
        if not sel:
            hint.config(text="请先选中一个坐标点")
            return
        pt_idx = sel[0]
        items = _get_items(state["cat"])
        pts = _item_points(items[state["idx"]], state["cat"])
        if pt_idx >= len(pts):
            return
        px, py = pts[pt_idx]["x"], pts[pt_idx]["y"]
        val = simpledialog.askstring(
            "编辑坐标", f"输入新坐标（当前 x={px:.0f}, y={py:.0f}）\n格式: x, y",
            parent=win, initialvalue=f"{px:.0f}, {py:.0f}")
        if not val:
            return
        parts = val.replace("，", ",").replace(" ", "").split(",")
        if len(parts) != 2:
            hint.config(text="格式错误：请输入 x, y")
            return
        try:
            nx, ny = int(float(parts[0])), int(float(parts[1]))
        except ValueError:
            hint.config(text="格式错误：请输入数字 x, y")
            return
        pts[pt_idx]["x"] = nx
        pts[pt_idx]["y"] = ny
        if state["cat"] == "platform":
            _sync_platform_derived(items[state["idx"]])
        _save()
        _redraw()
        _refresh_pt_list()
        hint.config(text=f"已更新坐标点 ({nx}, {ny})")

    def _on_delete() -> None:
        if state["cat"] is None or state["idx"] < 0:
            hint.config(text="请先在左侧选择一个标记项")
            return
        sel = pt_list.curselection()
        if not sel:
            hint.config(text="请先选中一个坐标点")
            return
        pt_idx = sel[0]
        items = _get_items(state["cat"])
        pts = _item_points(items[state["idx"]], state["cat"])
        if pt_idx >= len(pts):
            return
        pts.pop(pt_idx)
        if state["cat"] == "platform":
            if items[state["idx"]].get("turning_points"):
                _sync_platform_derived(items[state["idx"]])
        # 坐标点删空 → 移除该项
        if len(pts) == 0:
            if not messagebox.askyesno(
                    "删除标记", f"该标记的坐标点已删空，将删除整个标记项，确认？",
                    parent=win):
                pts.append({"x": 0, "y": 0})  # 撤销（恢复一个占位点，避免误删）
                _refresh_pt_list()
                return
            items.pop(state["idx"])
            state["idx"] = -1
            state["cat"] = None
        _save()
        _rebuild_tree()
        _redraw()
        _refresh_pt_list()
        hint.config(text="已删除坐标点，地图已同步更新")

    def _on_double_click(_evt) -> None:
        _on_edit()

    pt_list.bind("<Double-Button-1>", _on_double_click)

    _rebuild_tree()
    _redraw()
    _refresh_pt_list()
    app.root.wait_window(win)
