"""地图标记工具 — GUI 主类。

整合所有功能模块：窗口捕获、玩家检测、标记检测器、锚点系统、绘图。
"""

import json
import os
import threading
import time
import tkinter as tk
from pathlib import Path

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from .anchor_system import AnchorResolver
    from .config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR, WINDOW_TITLE
    from .detectors import FlashDetector, JumpDetector, PlatformRecorder, RopeDetector
    from .drawing import (
        draw_flash_preview,
        draw_jump_preview,
        draw_markers_overview,
        draw_platform_preview,
        draw_rope_preview,
    )
    from .markers import FlashMixin, JumpMixin, PlatformMixin, RopeMixin
    from .model_generator import open_model_generator
    from .patrol_route_editor import open_patrol_route_editor
    from .player_detection import PlayerTracker, detect_player_dot
    from .rdp_simplify import rdp_simplify
    from .viewer import open_viewer
    from .window_utils import find_window_by_title, force_foreground
except ImportError:
    from anchor_system import AnchorResolver  # type: ignore[no-redef]
    from config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR, WINDOW_TITLE  # type: ignore[no-redef]
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
    from model_generator import open_model_generator  # type: ignore[no-redef]
    from patrol_route_editor import open_patrol_route_editor  # type: ignore[no-redef]
    from player_detection import PlayerTracker, detect_player_dot  # type: ignore[no-redef]
    from rdp_simplify import rdp_simplify  # type: ignore[no-redef]
    from viewer import open_viewer  # type: ignore[no-redef]
    from window_utils import find_window_by_title, force_foreground  # type: ignore[no-redef]


class MapMarkerApp(PlatformMixin, RopeMixin, JumpMixin, FlashMixin):
    """统一标记工具: 小地图标记 / 平台标记 / 绳梯标记 / 跳跃点标记 / 闪现点标记"""

    MODES = ("minimap", "platform", "rope", "jump", "flash")

    PLATFORM_Y_OFFSET = 2
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
        self._mm_snapshot = None  # PIL Image, 小地图背景缓存
        self.status_text = tk.StringVar(value="请输入地图名称并点击确定")
        self.mm_offsets = (0, 0, 0, 0)
        self.mm_size = (0, 0)
        self.map_confirmed = False
        self.minimap_marked = False
        self._mode = None

        # ---- UI ----
        root.title("地图标记工具")
        root.geometry("500x520")
        root.resizable(False, False)

        tk.Label(root, text="地图标记工具",
                 font=("Microsoft YaHei", 14, "bold")).pack(pady=(12, 8))

        # Map name + confirm button
        f1 = tk.Frame(root)
        f1.pack(pady=3)
        tk.Label(f1, text="地图名称：", font=("Microsoft YaHei", 10), width=10,
                 anchor="e").pack(side="left")
        self.map_name_var = tk.StringVar(value="")
        self.map_name_entry = tk.Entry(f1, textvariable=self.map_name_var,
                                      font=("Microsoft YaHei", 10), width=18)
        self.map_name_entry.pack(side="left", padx=(5, 0))
        self.map_name_entry.bind("<Return>", lambda e: self._on_map_confirm())
        self.confirm_btn = tk.Button(f1, text="确定", font=("Microsoft YaHei", 9),
                                     width=5, cursor="hand2", command=self._on_map_confirm)
        self.confirm_btn.pack(side="left", padx=(4, 0))

        # Minimap marking button (between map name and coords)
        self.mode_buttons = {}
        f_mm = tk.Frame(root)
        f_mm.pack(pady=3)
        btn_mm = tk.Button(f_mm, text="小地图标记", font=("Microsoft YaHei", 10, "bold"),
                           width=12, height=1, bg="#3498db", fg="white",
                           activebackground="#2980b9", relief="flat", cursor="hand2",
                           command=self._on_minimap_mark)
        btn_mm.pack(side="left", padx=2)
        self.mode_buttons["minimap"] = btn_mm

        btn_view = tk.Button(f_mm, text="查看标记", font=("Microsoft YaHei", 10, "bold"),
                             width=12, height=1, bg="#27ae60", fg="white",
                             activebackground="#1e8449", relief="flat", cursor="hand2",
                             command=lambda: open_viewer(self))
        btn_view.pack(side="left", padx=2)
        self.mode_buttons["view"] = btn_view

        btn_model = tk.Button(f_mm, text="模型生成", font=("Microsoft YaHei", 10, "bold"),
                              width=12, height=1, bg="#8e44ad", fg="white",
                              activebackground="#6c3483", relief="flat", cursor="hand2",
                              command=lambda: open_model_generator(self))
        btn_model.pack(side="left", padx=2)
        self.mode_buttons["model"] = btn_model

        btn_patrol = tk.Button(f_mm, text="巡逻路线", font=("Microsoft YaHei", 10, "bold"),
                                width=12, height=1, bg="#e74c3c", fg="white",
                                activebackground="#c0392b", relief="flat", cursor="hand2",
                                command=lambda: open_patrol_route_editor(self))
        btn_patrol.pack(side="left", padx=2)
        self.mode_buttons["patrol"] = btn_patrol

        # Minimap coords
        f2 = tk.Frame(root)
        f2.pack(pady=3)
        tk.Label(f2, text="小地图坐标：", font=("Microsoft YaHei", 9),
                 width=10, anchor="e").pack(side="left")
        self.mm_left_var = tk.StringVar(value="")
        tk.Entry(f2, textvariable=self.mm_left_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        self.mm_top_var = tk.StringVar(value="")
        tk.Entry(f2, textvariable=self.mm_top_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        tk.Label(f2, text="~", font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        self.mm_right_var = tk.StringVar(value="")
        tk.Entry(f2, textvariable=self.mm_right_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        self.mm_bottom_var = tk.StringVar(value="")
        tk.Entry(f2, textvariable=self.mm_bottom_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)

        # Game window
        self.lbl_window = tk.Label(root, text="游戏: (检测中...)",
                                   font=("Microsoft YaHei", 9), fg="#888")
        self.lbl_window.pack(pady=(5, 3))

        # Try auto-detect game window on startup
        self._find_game_window()

        # Separator
        tk.Frame(root, height=1, bg="#ccc").pack(fill="x", padx=20, pady=6)

        # Function buttons grid
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

        # Separator
        tk.Frame(root, height=1, bg="#ccc").pack(fill="x", padx=20, pady=6)

        # Status
        tk.Label(root, textvariable=self.status_text,
                 font=("Microsoft YaHei", 9), fg="#333").pack(pady=(2, 6))

    # ==================== Helpers ====================

    def _find_game_window(self):
        """Try to auto-detect the game window and update the label."""
        win = find_window_by_title(WINDOW_TITLE)
        if win:
            hwnd, title, gl, gt, gr, gb = win
            self.target_hwnd = hwnd
            self.lbl_window.config(
                text=f"游戏: {title[:40]}  ({gr-gl}x{gb-gt})",
                fg="#2ecc71")
        else:
            self.lbl_window.config(text=f"游戏: 未找到 '{WINDOW_TITLE}'", fg="#e74c3c")

    def _set_mode_buttons(self, state, except_key=None):
        for key, btn in self.mode_buttons.items():
            if key == except_key:
                continue
            btn.config(state=state)

    def _ensure_mm_snapshot(self) -> None:
        """确保小地图背景截图已缓存，用于查看/模型/巡逻功能。

        仅在 _mm_snapshot 为 None 时截取一次。
        """
        if self._mm_snapshot is not None:
            return
        if self.target_hwnd is None:
            return
        ml, mt, mr, mb = self.mm_offsets
        mw, mh = mr - ml, mb - mt
        if mw <= 0 or mh <= 0:
            return
        try:
            import ctypes, mss, numpy as np
            r = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
            gl, gt = r.left, r.top
            region = {"left": gl + ml, "top": gt + mt, "width": mw, "height": mh}
            sct = mss.mss()
            img_raw = sct.grab(region)
            mm = np.array(img_raw)[:, :, :3]
            self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
        except Exception:
            pass

    def _load_map_config(self, map_name):
        """从 maps.json 读取指定地图配置，不存在返回 None。"""
        if not MAPS_FILE.exists():
            return None
        try:
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(map_name)
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
            win = find_window_by_title(WINDOW_TITLE)
            if win is None:
                self.status_text.set(f"未找到游戏窗口 '{WINDOW_TITLE}'，请先打开游戏")
                return False
            hwnd, title, gl, gt, gr, gb = win
            self.target_hwnd = hwnd
            self.lbl_window.config(text=f"游戏: {title[:40]}  ({gr-gl}x{gb-gt})")
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
                if ropes: parts.append(f"{len(ropes)}条绳梯")
                if platforms: parts.append(f"{len(platforms)}平台")
                if jumps: parts.append(f"{len(jumps)}跳跃点")
                if flash_points: parts.append(f"{len(flash_points)}闪现点")
                if len(parts) == 1: parts.append("尚无标记数据")
                if not self.minimap_marked: parts.append("需先标记小地图")
                self.status_text.set(" | ".join(parts))

            self.map_confirmed = True
            self.map_name_entry.config(state="disabled")
            self.confirm_btn.config(text="更改")
        else:
            self.map_confirmed = False
            self.minimap_marked = False
            self._clear_mm_coords()
            self.map_name_entry.config(state="normal")
            self.confirm_btn.config(text="确定")
            self.status_text.set("请输入地图名称并点击确定")

    # ==================== 1. Minimap marking ====================

    def _on_minimap_mark(self):
        if self.running:
            return
        if not self.map_confirmed:
            self.status_text.set("请先输入地图名称并点击确定")
            return
        win = find_window_by_title(WINDOW_TITLE)
        if win is None:
            self.status_text.set(f"未找到窗口 '{WINDOW_TITLE}'，请先打开游戏")
            return
        hwnd, title, gl, gt, gr, gb = win
        self.target_hwnd = hwnd
        self.lbl_window.config(text=f"游戏: {title[:40]}  ({gr-gl}x{gb-gt})")

        try:
            force_foreground(hwnd)
        except Exception:
            pass
        time.sleep(0.4)

        import ctypes
        sct = mss.mss()
        region = {"left": max(0, gl), "top": max(0, gt),
                  "width": gr - gl, "height": gb - gt}
        img = np.array(sct.grab(region))[:, :, :3]

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

        self.minimap_marked = True
        self.status_text.set(f"已框选小地图: ({x1},{y1})-({x2},{y2}) {rw}x{rh}px")
