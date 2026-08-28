"""地图标记工具 — GUI 主类。

整合所有功能模块：窗口捕获、玩家检测、标记检测器、锚点系统、绘图。
"""

import json
import ctypes
import os
import threading
import time
import tkinter as tk
from pathlib import Path

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

# ---- 导入 ----

try:
    from .anchor_system import AnchorResolver
    from .config import (CAPTURE_FPS, OUTPUT_DIR, SYSTEM_SETTINGS_FILE,
                         get_map_path, get_model_path,
                         WINDOW_TITLE as DEFAULT_WINDOW_KEYWORD)
    from .detectors import FlashDetector, JumpDetector, PlatformRecorder, RopeDetector
    from .drawing import (
        draw_flash_preview,
        draw_jump_preview,
        draw_markers_overview,
        draw_platform_preview,
        draw_rope_preview,
    )
    from .markers import FlashMixin, JumpMixin, PlatformMixin, RopeMixin
    from .minimap_stitch_dialog import (compute_world_pos, load_full_minimap,
                                         locate_in_full_map, open_minimap_stitch)
    from .model_generator import open_model_generator
    from .patrol_route_editor import open_patrol_route_editor
    from .player_detection import PlayerTracker, detect_player_dot
    from .rdp_simplify import rdp_simplify
    from .viewer import open_viewer
    from .window_utils import (capture_client, find_window_by_title,
                               find_windows_by_title, enum_visible_windows,
                               force_foreground)
except ImportError:
    from anchor_system import AnchorResolver  # type: ignore[no-redef]
    from config import (CAPTURE_FPS, OUTPUT_DIR, SYSTEM_SETTINGS_FILE,
                        get_map_path, get_model_path,
                        WINDOW_TITLE as DEFAULT_WINDOW_KEYWORD)  # type: ignore[no-redef]
    from detectors import (  # type: ignore[no-redef]
        FlashDetector,
        JumpDetector,
        PlatformRecorder,
        RopeDetector,
    )
    from drawing import (  # type: ignore[no-redef]
        draw_flash_preview,
        draw_jump_preview,
        draw_markers_overview,
        draw_platform_preview,
        draw_rope_preview,
    )
    from markers import FlashMixin, JumpMixin, PlatformMixin, RopeMixin  # type: ignore[no-redef]
    from minimap_stitch_dialog import (  # type: ignore[no-redef]
        compute_world_pos,
        load_full_minimap,
        locate_in_full_map,
        open_minimap_stitch,
    )
    from model_generator import open_model_generator  # type: ignore[no-redef]
    from patrol_route_editor import open_patrol_route_editor  # type: ignore[no-redef]
    from player_detection import PlayerTracker, detect_player_dot  # type: ignore[no-redef]
    from rdp_simplify import rdp_simplify  # type: ignore[no-redef]
    from viewer import open_viewer  # type: ignore[no-redef]
    from window_utils import (capture_client, find_window_by_title,
                              find_windows_by_title, enum_visible_windows,
                              force_foreground)  # type: ignore[no-redef]


class MapMarkerApp(PlatformMixin, RopeMixin, JumpMixin, FlashMixin):
    """统一标记工具: 小地图标记 / 平台标记 / 绳梯标记 / 跳跃点标记 / 闪现点标记"""

    MODES = ("minimap", "platform", "rope", "jump", "flash")
    PLATFORM_RDP_EPSILON = 2.5

    def __init__(self, root):
        self.root = root
        self.running = False
        self.thread = None
        self.target_hwnd = None
        self.player_tracker = PlayerTracker()
        self.platform_recorder = PlatformRecorder()
        self.rope_detector = RopeDetector()
        self.jump_detector = JumpDetector()
        self.flash_detector = FlashDetector()
        self.frame_count = 0
        self._mm_snapshot = None  # PIL Image, 小地图背景缓存（优先完整小地图）
        self._full_minimap_np = None  # 完整小地图内容区 BGR（用于在线定位），无则为 None
        self._full_trim = 0  # 完整小地图裁边量（与拼接一致）
        self._full_minimap_stale = False  # 重新框选后旧完整图失效，需重新合成
        self.status_text = tk.StringVar(value="请输入地图名称并点击确定")
        self.mm_offsets = (0, 0, 0, 0)
        self.mm_size = (0, 0)
        self.mode_buttons: dict = {}  # 先初始化，GUI 各按钮随后注册
        self.map_confirmed = False
        self.minimap_marked = False
        self._mode = None

        # ---- UI ----
        root.title("地图标记工具")
        root.geometry("500x590")
        root.resizable(False, False)

        tk.Label(root, text="地图标记工具",
                 font=("Microsoft YaHei", 14, "bold")).pack(pady=(12, 8))

        # ---- Row 1: 游戏窗口 ----
        f_win = tk.Frame(root)
        f_win.pack(pady=(5, 0))
        tk.Label(f_win, text="游戏窗口：", font=("Microsoft YaHei", 9),
                 anchor="e").pack(side="left")
        self._window_var = tk.StringVar(value="冒险岛怀旧服")
        self._window_entry = tk.Entry(f_win, textvariable=self._window_var,
                                      font=("Microsoft YaHei", 9), width=22)
        self._window_entry.pack(side="left", padx=(4, 0))
        self._window_entry.bind("<Return>", lambda e: self._find_game_window())
        self._window_entry.bind("<FocusOut>", lambda e: self._find_game_window())
        btn_pick = tk.Button(f_win, text="选择窗口", font=("Microsoft YaHei", 9),
                             width=8, cursor="hand2", command=self._pick_window)
        btn_pick.pack(side="left", padx=(4, 0))

        # ---- Row 2: 已连接状态 ----
        self.lbl_window = tk.Label(root, text="",
                                   font=("Microsoft YaHei", 9), fg="#888")
        self.lbl_window.pack(pady=(0, 3))

        # ---- Row 3: 地图名称 ----
        from tkinter import ttk
        f_map = tk.Frame(root)
        f_map.pack(pady=3)
        tk.Label(f_map, text="地图名称：", font=("Microsoft YaHei", 10),
                 anchor="e").pack(side="left")
        self.map_name_var = tk.StringVar(value="")
        self.map_name_combo = ttk.Combobox(f_map, textvariable=self.map_name_var,
                                            font=("Microsoft YaHei", 10), width=18)
        self.map_name_combo.pack(side="left", padx=(4, 4))
        self.map_name_combo.bind("<Return>", lambda e: self._on_map_confirm())
        self.confirm_btn = tk.Button(f_map, text="确定", font=("Microsoft YaHei", 9),
                                     width=5, cursor="hand2", command=self._on_map_confirm)
        self.confirm_btn.pack(side="left")

        # ---- Row 4: 小地图坐标 ----
        f_mmc = tk.Frame(root)
        f_mmc.pack(pady=3)
        tk.Label(f_mmc, text="小地图坐标：", font=("Microsoft YaHei", 9),
                 anchor="e").pack(side="left")
        self.mm_left_var = tk.StringVar(value="")
        tk.Entry(f_mmc, textvariable=self.mm_left_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        self.mm_top_var = tk.StringVar(value="")
        tk.Entry(f_mmc, textvariable=self.mm_top_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        self.mm_right_var = tk.StringVar(value="")
        tk.Entry(f_mmc, textvariable=self.mm_right_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        self.mm_bottom_var = tk.StringVar(value="")
        tk.Entry(f_mmc, textvariable=self.mm_bottom_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        # 小地图标记按钮（缩小，跟在坐标后面）
        btn_mm_small = tk.Button(f_mmc, text="标记", font=("Microsoft YaHei", 9),
                                 width=4, height=1, bg="#3498db", fg="white",
                                 activebackground="#2980b9", relief="flat", cursor="hand2",
                                 command=self._on_minimap_mark)
        btn_mm_small.pack(side="left", padx=(4, 0))
        self.mode_buttons["minimap"] = btn_mm_small

        # ---- 分隔 ----
        tk.Frame(root, height=1, bg="#ccc").pack(fill="x", padx=20, pady=6)

        # ---- Row 6: 小地图合成 查看标记 世界模型 巡逻路线 ----
        f_tools = tk.Frame(root)
        f_tools.pack(pady=3)
        btn_mms = tk.Button(f_tools, text="小地图合成", font=("Microsoft YaHei", 10, "bold"),
                            width=12, height=1, bg="#16a085", fg="white",
                            activebackground="#117864", relief="flat", cursor="hand2",
                            command=lambda: open_minimap_stitch(self))
        btn_mms.pack(side="left", padx=2)

        btn_view = tk.Button(f_tools, text="查看标记", font=("Microsoft YaHei", 10, "bold"),
                             width=12, height=1, bg="#27ae60", fg="white",
                             activebackground="#1e8449", relief="flat", cursor="hand2",
                             command=lambda: open_viewer(self))
        btn_view.pack(side="left", padx=2)
        self.mode_buttons["view"] = btn_view

        btn_model = tk.Button(f_tools, text="世界模型", font=("Microsoft YaHei", 10, "bold"),
                              width=12, height=1, bg="#8e44ad", fg="white",
                              activebackground="#6c3483", relief="flat", cursor="hand2",
                              command=lambda: open_model_generator(self))
        btn_model.pack(side="left", padx=2)
        self.mode_buttons["model"] = btn_model

        btn_patrol = tk.Button(f_tools, text="巡逻路线", font=("Microsoft YaHei", 10, "bold"),
                                width=12, height=1, bg="#e74c3c", fg="white",
                                activebackground="#c0392b", relief="flat", cursor="hand2",
                                command=lambda: open_patrol_route_editor(self))
        btn_patrol.pack(side="left", padx=2)
        self.mode_buttons["patrol"] = btn_patrol

        # ---- Row 7: HP/MP / 搜索范围 ----
        f_tmpl = tk.Frame(root)
        f_tmpl.pack(pady=1)

        btn_hpmp = tk.Button(f_tmpl, text="HP/MP标记", font=("Microsoft YaHei", 10, "bold"),
                             width=12, height=1, bg="#7f8c8d", fg="white",
                             activebackground="#666", relief="flat", cursor="hand2",
                             command=self._on_hpmp_mark)
        btn_hpmp.pack(side="left", padx=2)
        self.mode_buttons["hpmp"] = btn_hpmp

        btn_search = tk.Button(f_tmpl, text="搜索范围标记", font=("Microsoft YaHei", 10, "bold"),
                                width=14, height=1, bg="#7f8c8d", fg="white",
                                activebackground="#666", relief="flat", cursor="hand2",
                                command=self._on_search_region_mark)
        btn_search.pack(side="left", padx=2)
        self.mode_buttons["search_region"] = btn_search

        # ---- 分隔 ----
        tk.Frame(root, height=1, bg="#ccc").pack(fill="x", padx=20, pady=6)

        # ---- Row 9-10: 平台 绳梯 / 跳跃 闪现 ----
        grid = tk.Frame(root)
        grid.pack(pady=4)

        btn_specs = [
            ("platform",  "平台标记",     "#9b59b6", self._on_platform_toggle),
            ("rope",      "绳梯标记",     "#e67e22", self._on_rope_toggle),
            ("jump",      "跳跃点标记",   "#1abc9c", self._on_jump_toggle),
            ("flash",     "闪现点标记",   "#f39c12", self._on_flash_toggle),
        ]
        for i, (key, text, color, cmd) in enumerate(btn_specs):
            row, col = divmod(i, 2)
            btn = tk.Button(grid, text=text, font=("Microsoft YaHei", 11, "bold"),
                            width=18, height=2, bg=color, fg="white",
                            activebackground=color, relief="flat", cursor="hand2",
                            command=cmd)
            btn.grid(row=row, column=col, padx=4, pady=4)
            self.mode_buttons[key] = btn

        # ---- 分隔 ----
        tk.Frame(root, height=1, bg="#ccc").pack(fill="x", padx=20, pady=6)

        # Status
        tk.Label(root, textvariable=self.status_text,
                 font=("Microsoft YaHei", 9), fg="#333").pack(pady=(2, 6))

        # Auto-detect on startup
        self._find_game_window()

    def _refresh_map_dropdown(self):
        """扫描 {窗口名}/ 目录下已有的 *_maps.json 作为下拉选项。"""
        win_name = self._window_var.get().strip()
        if not win_name:
            self.map_name_combo["values"] = []
            return
        safe_win = win_name
        for ch in '<>:"/\\|?*':
            safe_win = safe_win.replace(ch, '_')
        mm_dir = OUTPUT_DIR / safe_win
        maps = []
        if mm_dir.is_dir():
            for f in sorted(mm_dir.iterdir()):
                if f.name.endswith("_maps.json"):
                    maps.append(f.name[:-len("_maps.json")])
        self.map_name_combo["values"] = maps

    # ==================== Helpers ====================

    def _find_game_window(self):
        """Auto-detect game window by keyword, update label & map dropdown."""
        keyword = self._window_var.get().strip()
        if not keyword:
            self.target_hwnd = None
            self.lbl_window.config(text="未找到游戏窗口", fg="#e74c3c")
            return
        win = find_window_by_title(keyword)
        if win:
            hwnd, title, gl, gt, gr, gb = win
            self.target_hwnd = hwnd
            self._window_var.set(title)
            self.lbl_window.config(
                text=f"已连接: {title}  ({gr-gl}x{gb-gt})",
                fg="#2ecc71")
            # 强制前置
            try:
                force_foreground(hwnd)
            except Exception:
                pass
        else:
            self.target_hwnd = None
            self.lbl_window.config(text=f"未找到 '{keyword}'", fg="#e74c3c")
        # 刷新地图下拉列表
        self._refresh_map_dropdown()

    def _get_window_rect(self):
        """获取目标窗口当前矩形区域。

        Returns:
            (left, top, right, bottom) or None
        """
        if self.target_hwnd is None:
            return None
        try:
            r = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
            return (r.left, r.top, r.right, r.bottom)
        except Exception:
            return None

    def _update_window_label(self, win_info=None):
        """更新窗口状态标签。"""
        if self.target_hwnd is None:
            self.lbl_window.config(text="状态: 未选择窗口", fg="#888")
            return
        try:
            r = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            title = self._window_var.get()[:40]
            self.lbl_window.config(text=f"已连接: {title}  ({w}x{h})", fg="#2ecc71")
        except Exception:
            self.lbl_window.config(text="状态: 窗口信息获取失败", fg="#e74c3c")

    def _pick_window(self):
        """按输入的关键词模糊匹配选择窗口。"""
        title = self._window_var.get().strip()
        if not title:
            self.status_text.set("请先输入窗口标题关键词")
            return
        windows = find_windows_by_title(title)
        if not windows:
            self.status_text.set(f"未找到包含 '{title}' 的窗口")
            return
        if len(windows) == 1:
            self.target_hwnd = windows[0][0]
            self._window_var.set(windows[0][1])
            self._update_window_label()
            self.status_text.set(f"已选择窗口: {windows[0][1][:40]}")
            return
        self._show_window_dialog(windows, f"选择窗口 — 匹配 '{title}'")

    def _browse_windows(self):
        """列出所有可见窗口供选择。"""
        windows = enum_visible_windows(200, 200)
        if not windows:
            self.status_text.set("未找到可用窗口 (>=200x200)")
            return
        self._show_window_dialog(windows, "浏览窗口")

    def _show_window_dialog(self, windows: list, title: str = "选择窗口"):
        """显示窗口选择对话框。

        Args:
            windows: [(hwnd, title, left, top, right, bottom, pid), ...]
            title: 对话框标题
        """
        result = [None]

        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("700x380")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        tk.Label(dlg, text=f"找到 {len(windows)} 个窗口，请选择:",
                 font=("Microsoft YaHei", 10)).pack(pady=(10, 4))

        list_frame = tk.Frame(dlg)
        list_frame.pack(fill="both", expand=True, padx=12, pady=4)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        lb = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                        font=("Courier", 9), width=95, height=14)
        scrollbar.config(command=lb.yview)
        lb.pack(fill="both", expand=True)

        for i, (_, wt, l, t, r, b, pid) in enumerate(windows):
            wt_short = wt[:52] + ("..." if len(wt) > 52 else "")
            lb.insert("end", f"[{i:02d}] {wt_short:<55} {r-l}x{b-t:<10} PID={pid}")
        lb.selection_set(0)

        def _on_ok():
            sel = lb.curselection()
            if sel:
                result[0] = windows[sel[0]]
            dlg.destroy()

        lb.bind("<Double-Button-1>", lambda e: _on_ok())

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=(4, 10))
        tk.Button(btn_frame, text="确定", font=("Microsoft YaHei", 9),
                  width=10, command=_on_ok).pack(side="left", padx=4)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 9),
                  width=10, command=dlg.destroy).pack(side="left", padx=4)

        # 居中于父窗口
        dlg.update_idletasks()
        px = self.root.winfo_rootx()
        py = self.root.winfo_rooty()
        pw = self.root.winfo_width()
        ph = self.root.winfo_height()
        dw = dlg.winfo_width()
        dh = dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw) // 2}+{py + (ph - dh) // 2}")

        dlg.wait_window()

        if result[0] is not None:
            hwnd, wt, l, t, r, b, pid = result[0]
            self.target_hwnd = hwnd
            self._window_var.set(wt)
            self._update_window_label()
            self.status_text.set(f"已选择窗口: {wt[:40]}")

    def _set_mode_buttons(self, state, except_key=None):
        for key, btn in self.mode_buttons.items():
            if key == except_key:
                continue
            btn.config(state=state)

    def _ensure_mm_snapshot(self) -> None:
        """确保小地图背景截图已缓存，用于查看/模型/巡逻功能。

        优先加载该地图的完整小地图（已通过"小地图合成"保存）；没有时
        回退为实时截取局部小地图（整图模式/未合成地图）。
        仅在 _mm_snapshot 为 None 时加载一次。
        """
        if self._mm_snapshot is not None:
            return
        if self.target_hwnd is None:
            return
        if self._try_load_full_minimap():
            return
        ml, mt, mr, mb = self.mm_offsets
        mw, mh = mr - ml, mb - mt
        if mw <= 0 or mh <= 0:
            return
        try:
            frame = capture_client(self.target_hwnd)
            if frame is None:
                return
            mm = frame[mt:mb, ml:mr]
            self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
        except Exception:
            pass

    def _try_load_full_minimap(self) -> bool:
        """尝试加载该地图的完整小地图作为标记背景。

        成功时更新 _mm_snapshot（完整框背景）与 mm_size（完整框尺寸），
        并缓存内容区 ndarray 供在线定位。返回是否成功。
        """
        if self._full_minimap_stale:
            return False  # 重新框选后旧完整图失效，需重新合成
        win_name = self._window_var.get().strip()
        map_name = self.map_name_var.get().strip()
        if not win_name or not map_name:
            return False
        info = load_full_minimap(win_name, map_name)
        if info is None:
            return False
        self._full_minimap_np = info["content"]
        self._full_trim = info["trim"]
        self._mm_snapshot = info["full_pil"]
        self.mm_size = info["world_size"]
        return True

    def _to_world_pos(self, mm, pos):
        """局部小地图内黄点坐标 → 世界坐标（完整小地图框坐标系）。

        mm: 当前帧局部小地图（BGR 原图，含边框）
        pos: detect_player_dot 返回的黄点局部坐标 (x, y)

        Returns:
            (world_x, world_y) 世界坐标；定位失败返回 None（调用方应丢弃该帧）。
            无完整小地图（整图模式）时直接返回原坐标。
        """
        if self._full_minimap_np is None:
            return float(pos[0]), float(pos[1])
        loc = locate_in_full_map(mm, self._full_minimap_np, trim=self._full_trim)
        if loc is None:
            return None
        return compute_world_pos(pos, loc, trim=self._full_trim)

    def _load_map_config(self, map_name):
        """从 {窗口名}/{地图名}_maps.json 读取配置，不存在返回 None。"""
        win_name = self._window_var.get().strip()
        if not win_name:
            return None
        map_path = get_map_path(win_name, map_name)
        if not map_path.exists():
            return None
        try:
            with open(map_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _clear_mm_coords(self):
        self.mm_left_var.set("")
        self.mm_top_var.set("")
        self.mm_right_var.set("")
        self.mm_bottom_var.set("")
        self.mm_offsets = (0, 0, 0, 0)
        self.mm_size = (0, 0)

    def _check_minimap_ready(self) -> bool:
        """功能按钮点击前的统一检查：游戏窗口 + 地图已确认 + 小地图已标记。"""
        if not self.map_confirmed:
            self.status_text.set("请先输入地图名称并点击确定")
            return False
        if not self.minimap_marked:
            self.status_text.set("请先标记小地图")
            return False
        if self.target_hwnd is None:
            self.status_text.set("请先选择游戏窗口")
            return False
        return True

    # ==================== Map name confirm / change ====================

    def _on_map_confirm(self):
        if self.running:
            return

        if not self.map_confirmed:
            map_name = self.map_name_var.get().strip()
            if not map_name:
                self.status_text.set("请先输入地图名称")
                return

            config = self._load_map_config(map_name)
            if config is None:
                self._clear_mm_coords()
                self.minimap_marked = False
                self.status_text.set(f"地图 '{map_name}' 无已有配置，请先标记小地图")
            else:
                mm_region = config.get("mm_region")
                if mm_region and len(mm_region) == 4:
                    x1, y1, x2, y2 = mm_region
                    self.mm_left_var.set(str(x1))
                    self.mm_top_var.set(str(y1))
                    self.mm_right_var.set(str(x2))
                    self.mm_bottom_var.set(str(y2))
                    self.mm_offsets = (x1, y1, x2, y2)
                    self.mm_size = (x2 - x1, y2 - y1)
                    self.minimap_marked = True
                else:
                    self._clear_mm_coords()
                    self.minimap_marked = False

                ropes = config.get("ropes", [])
                platforms = config.get("platforms", [])
                jumps = config.get("jumps", [])
                flash_points = config.get("flash_points", [])
                parts = [f"已加载 '{map_name}' 配置"]
                if self._try_load_full_minimap():
                    parts.append(f"完整小地图 {self.mm_size[0]}x{self.mm_size[1]}")
                if ropes: parts.append(f"{len(ropes)}条绳梯")
                if platforms: parts.append(f"{len(platforms)}平台")
                if jumps: parts.append(f"{len(jumps)}跳跃点")
                if flash_points: parts.append(f"{len(flash_points)}闪现点")
                if len(parts) == 1: parts.append("尚无标记数据")
                if not self.minimap_marked: parts.append("需先标记小地图")
                self.status_text.set(" | ".join(parts))

            self.map_confirmed = True
            self.map_name_combo.config(state="disabled")
            self.confirm_btn.config(text="更改")
        else:
            self.map_confirmed = False
            self.minimap_marked = False
            self._clear_mm_coords()
            self._mm_snapshot = None
            self._full_minimap_np = None
            self._full_trim = 0
            self._full_minimap_stale = False
            self.map_name_combo.config(state="normal")
            self.confirm_btn.config(text="确定")
            self.status_text.set("请选择或输入地图名称")

    # ==================== 1. Minimap marking ====================

    def _on_minimap_mark(self):
        if self.running:
            return
        if not self.map_confirmed:
            self.status_text.set("请先输入地图名称并点击确定")
            return
        if self.target_hwnd is None:
            self.status_text.set("请先选择游戏窗口")
            return

        img = capture_client(self.target_hwnd)
        if img is None:
            self.status_text.set("截图失败")
            return

        max_dim = max(img.shape[:2])
        scale = max(1.2, min(2.0, 1600.0 / max_dim))
        interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_LINEAR
        disp = cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)

        cv2.namedWindow("Drag to select minimap, then press ENTER", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Drag to select minimap, then press ENTER",
                         disp.shape[1], disp.shape[0] + 30)
        roi = cv2.selectROI("Drag to select minimap, then press ENTER", disp, False)
        cv2.destroyAllWindows()

        if roi[2] == 0 or roi[3] == 0:
            self.status_text.set("已取消小地图框选")
            return

        rx, ry, rw, rh = roi
        x1 = int(rx / scale)
        y1 = int(ry / scale)
        x2 = x1 + int(rw / scale)
        y2 = y1 + int(rh / scale)

        self.mm_left_var.set(str(x1))
        self.mm_top_var.set(str(y1))
        self.mm_right_var.set(str(x2))
        self.mm_bottom_var.set(str(y2))
        self.mm_offsets = (x1, y1, x2, y2)
        self.mm_size = (x2 - x1, y2 - y1)
        # 框选位置改变 → 旧完整小地图失效，重置缓存（需重新合成后再使用）
        self._mm_snapshot = None
        self._full_minimap_np = None
        self._full_trim = 0
        self._full_minimap_stale = True

        self.minimap_marked = True
        self.status_text.set(f"已框选小地图(客户区坐标): ({x1},{y1})-({x2},{y2}) {rw}x{rh}px")

    def _on_hpmp_mark(self):
        """框选 HP 和 MP 文字区域，存入 system_setting.json。"""
        if self.running:
            return
        if self.target_hwnd is None:
            self.status_text.set("请先选择游戏窗口")
            return

        win_title = self._window_var.get().strip()
        if not win_title:
            self.status_text.set("无法获取窗口标题")
            return

        self.status_text.set("正在截取画面...")
        img = capture_client(self.target_hwnd)
        if img is None:
            self.status_text.set("截图失败")
            return

        win_h, win_w = img.shape[:2]
        max_dim = max(img.shape[:2])
        scale = max(1.2, min(2.0, 1600.0 / max_dim))
        interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_LINEAR
        disp = cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)

        # Step 1: 框选 HP 区域
        cv2.namedWindow("1/2: Drag to select HP area, press ENTER", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("1/2: Drag to select HP area, press ENTER",
                         disp.shape[1], disp.shape[0] + 30)
        hp_roi = cv2.selectROI("1/2: Drag to select HP area, press ENTER", disp, False)
        cv2.destroyAllWindows()

        if hp_roi[2] == 0 or hp_roi[3] == 0:
            self.status_text.set("已取消 HP/MP 标记")
            return

        hx1 = int(hp_roi[0] / scale)
        hy1 = int(hp_roi[1] / scale)
        hx2 = hx1 + int(hp_roi[2] / scale)
        hy2 = hy1 + int(hp_roi[3] / scale)

        # Step 2: 框选 MP 区域
        cv2.namedWindow("2/2: Drag to select MP area, press ENTER", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("2/2: Drag to select MP area, press ENTER",
                         disp.shape[1], disp.shape[0] + 30)
        mp_roi = cv2.selectROI("2/2: Drag to select MP area, press ENTER", disp, False)
        cv2.destroyAllWindows()

        if mp_roi[2] == 0 or mp_roi[3] == 0:
            self.status_text.set("已取消 HP/MP 标记")
            return

        mx1 = int(mp_roi[0] / scale)
        my1 = int(mp_roi[1] / scale)
        mx2 = mx1 + int(mp_roi[2] / scale)
        my2 = my1 + int(mp_roi[3] / scale)

        # 保存到 system_setting.json
        settings = {}
        if SYSTEM_SETTINGS_FILE.exists():
            try:
                with open(SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        entry = settings.get(win_title, {})
        if not isinstance(entry, dict):
            entry = {}
        entry["hp_rect"] = [hx1, hy1, hx2, hy2]
        entry["mp_rect"] = [mx1, my1, mx2, my2]
        entry["window_size"] = [win_w, win_h]
        settings[win_title] = entry

        SYSTEM_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SYSTEM_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

        self.status_text.set(
            f"已标记 HP/MP [{win_title[:25]}]: "
            f"HP({hx1},{hy1})-({hx2},{hy2}) "
            f"MP({mx1},{my1})-({mx2},{my2})"
        )

    def _on_search_region_mark(self):
        """标记角色搜索范围（避免搜到底部 UI 区域），按窗口名保存到 system_setting.json。"""
        if self.running:
            return
        if self.target_hwnd is None:
            self.status_text.set("请先选择游戏窗口")
            return

        win_title = self._window_var.get().strip()
        if not win_title:
            self.status_text.set("无法获取窗口标题")
            return

        img = capture_client(self.target_hwnd)
        if img is None:
            self.status_text.set("截图失败")
            return

        win_h, win_w = img.shape[:2]

        max_dim = max(img.shape[:2])
        scale = max(1.2, min(2.0, 1600.0 / max_dim))
        interp = cv2.INTER_CUBIC if scale > 1.0 else cv2.INTER_LINEAR
        disp = cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)

        cv2.namedWindow("Drag to select search region, then press ENTER",
                        cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Drag to select search region, then press ENTER",
                         disp.shape[1], disp.shape[0] + 30)
        roi = cv2.selectROI("Drag to select search region, then press ENTER",
                            disp, False)
        cv2.destroyAllWindows()

        if roi[2] == 0 or roi[3] == 0:
            self.status_text.set("已取消搜索范围框选")
            return

        rx, ry, rw, rh = roi
        x1 = int(rx / scale)
        y1 = int(ry / scale)
        x2 = x1 + int(rw / scale)
        y2 = y1 + int(rh / scale)
        rect_data = [x1, y1, x2, y2]

        settings = {}
        if SYSTEM_SETTINGS_FILE.exists():
            try:
                with open(SYSTEM_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # 合并更新
        entry = settings.get(win_title, {})
        if not isinstance(entry, dict):
            entry = {}
        entry["search_region"] = rect_data
        entry["window_size"] = [win_w, win_h]
        settings[win_title] = entry

        SYSTEM_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SYSTEM_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

        self.status_text.set(
            f"已标记搜索范围 [{win_title[:25]}]: ({x1},{y1})-({x2},{y2})"
        )
