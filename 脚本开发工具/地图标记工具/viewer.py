"""标记工具 — 标记查看器。

打开查看窗口显示所有已保存的标记。"""

import json
import tkinter as tk

from PIL import Image, ImageTk

try:
    from .config import MAPS_FILE
    from .drawing import draw_markers_overview
except ImportError:
    from config import MAPS_FILE  # type: ignore[no-redef]
    from drawing import draw_markers_overview  # type: ignore[no-redef]


def open_viewer(app) -> None:
    app._ensure_mm_snapshot()
    if app.running: app.status_text.set("标记运行中，请先停止"); return
    map_name: str = app.map_name_var.get().strip()
    if not map_name: app.status_text.set("请先输入地图名称"); return
    platforms: list = []; ropes: list = []; jumps: list = []; flashes: list = []
    map_cfg: dict = {}
    if MAPS_FILE.exists():
        with open(MAPS_FILE, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
        map_cfg = data.get(map_name, {})
        platforms = map_cfg.get("platforms", [])
        ropes = map_cfg.get("ropes", [])
        jumps = map_cfg.get("jumps", [])
        if not jumps: jumps = map_cfg.get("teleports", [])
        flashes = map_cfg.get("flash_points", [])
    if not platforms and not ropes and not jumps and not flashes:
        app.status_text.set(f"地图 '{map_name}' 尚无标记数据"); return
    mm_region = map_cfg.get("mm_region")
    if mm_region and len(mm_region) == 4: mw, mh = mm_region[2] - mm_region[0], mm_region[3] - mm_region[1]
    else:
        mw, mh = app.mm_size
        if mw <= 0 or mh <= 0: mw, mh = 154, 156
    scale: float = min(6.0, 700 / max(mw, mh, 1))
    dw: int = int(mw * scale); dh: int = int(mh * scale)
    img: Image.Image = draw_markers_overview(
        app._mm_snapshot, (mw, mh), map_name,
        platforms, ropes, jumps, flashes, target_size=(dw, dh))
    photo = ImageTk.PhotoImage(img)
    view_win = tk.Toplevel(app.root)
    view_win.title(f"查看标记 - {map_name}")
    view_win.transient(app.root); view_win.grab_set(); view_win.resizable(False, False)
    canvas = tk.Canvas(view_win, width=dw, height=dh, highlightthickness=0)
    canvas.pack(padx=5, pady=5)
    canvas.create_image(0, 0, anchor="nw", image=photo); canvas.photo = photo
    app.root.wait_window(view_win)
