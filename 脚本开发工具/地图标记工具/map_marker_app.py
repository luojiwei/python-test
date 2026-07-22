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
    from .player_detection import PlayerTracker, detect_player_dot
    from .rdp_simplify import rdp_simplify
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
    from player_detection import PlayerTracker, detect_player_dot  # type: ignore[no-redef]
    from rdp_simplify import rdp_simplify  # type: ignore[no-redef]
    from window_utils import find_window_by_title, force_foreground  # type: ignore[no-redef]


class MapMarkerApp:
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
                             command=self._on_view_markers)
        btn_view.pack(side="left", padx=2)
        self.mode_buttons["view"] = btn_view

        btn_model = tk.Button(f_mm, text="模型生成", font=("Microsoft YaHei", 10, "bold"),
                              width=12, height=1, bg="#8e44ad", fg="white",
                              activebackground="#6c3483", relief="flat", cursor="hand2",
                              command=self._on_model_generate)
        btn_model.pack(side="left", padx=2)
        self.mode_buttons["model"] = btn_model

        btn_patrol = tk.Button(f_mm, text="巡逻路线", font=("Microsoft YaHei", 10, "bold"),
                                width=12, height=1, bg="#e74c3c", fg="white",
                                activebackground="#c0392b", relief="flat", cursor="hand2",
                                command=self._on_patrol_route)
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

    # ==================== 2. Platform marking ====================

    def _on_platform_toggle(self):
        if self._mode == "rope":
            self.status_text.set("绳梯标记运行中，请先停止")
            return
        if self._mode == "jump":
            self.status_text.set("跳跃点标记运行中，请先停止")
            return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止")
            return
        if self._mode == "platform":
            self._platform_stop()
        else:
            self._platform_start()

    def _platform_start(self):
        if not self._check_minimap_ready():
            return
        map_name = self.map_name_var.get().strip()

        ml = int(self.mm_left_var.get())
        mt = int(self.mm_top_var.get())
        mr = int(self.mm_right_var.get())
        mb = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb)
        self.mm_size = (mr - ml, mb - mt)

        self.status_text.set(f"平台标记中... 记录角色位置 (地图: {map_name})")

        self.platform_recorder.reset()
        self.frame_count = 0
        self.running = True
        self._mode = "platform"
        self._platform_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="platform")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(
            target=self._loop_platform, args=(map_name,), daemon=True)
        self.thread.start()

    def _platform_stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self._platform_button_set_running(False)
        self._set_mode_buttons("normal")
        self.confirm_btn.config(state="normal")
        self._mode = None

        if self.platform_recorder.count > 0:
            self._platform_review_and_save()
        else:
            self.status_text.set("平台标记已停止 | 未记录到任何位置")

    def _platform_button_set_running(self, running):
        btn = self.mode_buttons["platform"]
        if running:
            btn.config(text="停止平台标记", bg="#ff6b6b", activebackground="#e85a5a")
        else:
            btn.config(text="平台标记", bg="#9b59b6", activebackground="#8e44ad")

    def _loop_platform(self, map_name):
        import ctypes
        sct = mss.MSS()
        interval = 1.0 / CAPTURE_FPS
        debug_frame_saved = False
        last_status = time.time()

        while self.running:
            t0 = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt:
                    time.sleep(0.1)
                    continue

                ml, mt, mr, mb = self.mm_offsets
                ml_abs = gl + ml
                mt_abs = gt + mt
                mw = mr - ml
                mh = mb - mt

                if mw <= 0 or mh <= 0:
                    time.sleep(0.1)
                    continue

                region = {"left": ml_abs, "top": mt_abs,
                          "width": mw, "height": mh}
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]

                if not debug_frame_saved:
                    os.makedirs(OUTPUT_DIR, exist_ok=True)
                    cv2.imwrite(str(OUTPUT_DIR / "debug_platform_mm.png"), mm)
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                    debug_frame_saved = True

                pos = detect_player_dot(mm, self.player_tracker)
                if pos:
                    self.platform_recorder.add(pos[0], pos[1])

                self.frame_count += 1

                now = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"平台标记中... {self.frame_count}帧 | "
                        f"记录{self.platform_recorder.count}个位置")
                    last_status = now

            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}")
                break

            sleep_t = interval - (time.time() - t0)
            if sleep_t > 0:
                time.sleep(sleep_t)

        msg = f"已停止  {self.frame_count}帧 | 记录{self.platform_recorder.count}个位置"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Platform Review & Save ====================

    def _platform_review_and_save(self):
        try:
            self._platform_review_and_save_impl()
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_platform_error.log", "w", encoding="utf-8") as f:
                f.write(err)
            self.status_text.set(f"审阅窗口错误: {e}")

    def _platform_review_and_save_impl(self):
        map_name = self.map_name_var.get().strip()
        positions = self.platform_recorder.get_positions()
        if not positions:
            self.status_text.set("无位置数据, 跳过保存")
            return

        existing_platforms = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing_platforms = data.get(map_name, {}).get("platforms", [])

        sw, sh = self.mm_size
        scale = min(6.0, 700 / max(sw, sh, 1))
        dw = int(sw * scale)
        dh = int(sh * scale)

        state = {
            "selected_platform_idx": 0,
            "preview_img": None,
            "delete_list": [],
        }

        check_vars = [tk.BooleanVar(value=True) for _ in range(len(positions))]

        def get_active_positions():
            return [positions[i] for i, v in enumerate(check_vars) if v.get()]

        def get_preview_positions_and_active():
            active = get_active_positions()
            active_set = set(active)
            all_pts = list(positions)
            if state["selected_platform_idx"] > 0:
                idx = state["selected_platform_idx"] - 1
                if idx < len(existing_platforms):
                    ep_pts = existing_platforms[idx].get("all_points", [])
                    all_pts.extend(ep_pts)
            return all_pts, active_set

        def draw_preview():
            all_pts, active_set = get_preview_positions_and_active()
            if not all_pts:
                all_pts = list(positions)
                active_set = set(positions)
            img = draw_platform_preview(self._mm_snapshot, self.mm_size,
                map_name, all_pts, target_size=(dw, dh), active_set=active_set)
            state["preview_img"] = ImageTk.PhotoImage(img)
            return state["preview_img"]

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅平台 - {map_name}")
        review_win.transient(self.root)
        review_win.grab_set()

        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        initial_img = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=initial_img)
        canvas.photo = initial_img

        def refresh_preview():
            new_img = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=new_img)
            canvas.photo = new_img

        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)

        tk.Label(right_frame, text=f"记录位置: {len(positions)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))

        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        inner = tk.Frame(list_frame)
        inner.pack(fill="both", expand=True)

        canvas_list = tk.Canvas(inner, width=280, height=200, highlightthickness=0)
        scrollbar = tk.Scrollbar(inner, orient="vertical", command=canvas_list.yview)
        pos_frame = tk.Frame(canvas_list)

        canvas_list.create_window((0, 0), window=pos_frame, anchor="nw")
        canvas_list.configure(yscrollcommand=scrollbar.set)

        def _on_frame_configure(event=None):
            canvas_list.configure(scrollregion=canvas_list.bbox("all"))
        pos_frame.bind("<Configure>", _on_frame_configure)

        canvas_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def refresh_position_list():
            for widget in pos_frame.winfo_children():
                widget.destroy()
            for i, (x, y) in enumerate(positions):
                var = check_vars[i]
                row = tk.Frame(pos_frame)
                row.pack(fill="x", pady=1)
                chk = tk.Checkbutton(row, variable=var,
                    font=("Consolas", 9), text=f"P{i+1:03d}: ({x:>3}, {y:>3})",
                    command=refresh_preview)
                chk.pack(side="left", padx=2)
                btn = tk.Button(row, text="×", font=("Microsoft YaHei", 8, "bold"),
                    fg="#e74c3c", width=2, command=lambda idx=i: _remove_position(idx))
                btn.pack(side="right", padx=2)

        def _remove_position(idx):
            nonlocal positions
            positions.pop(idx)
            check_vars.pop(idx)
            refresh_position_list()
            refresh_preview()

        refresh_position_list()

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=8)

        tk.Label(right_frame, text="保存到:", font=("Microsoft YaHei", 9, "bold")
                 ).pack(anchor="w")
        radio_var = tk.IntVar(value=0)

        radio_frame = tk.Frame(right_frame)
        radio_frame.pack(anchor="w", pady=4)
        tk.Radiobutton(radio_frame, text="新平台",
            variable=radio_var, value=0, font=("Microsoft YaHei", 9),
            command=lambda: _on_radio_change(0)).pack(anchor="w")

        for i, plat in enumerate(existing_platforms):
            ep = plat.get("left_endpoint", {})
            pcount = len(plat.get("all_points", []))
            label = f"平台{i+1}: ({ep.get('x','?')},{ep.get('y','?')}) {pcount}点"
            tk.Radiobutton(radio_frame, text=label,
                variable=radio_var, value=i + 1, font=("Microsoft YaHei", 9),
                command=lambda idx=i+1: _on_radio_change(idx)).pack(anchor="w")

        def _on_radio_change(idx):
            state["selected_platform_idx"] = idx
            refresh_preview()

        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=10)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"),
                  width=10, bg="#4ecdc4", fg="white",
                  command=lambda: _save_and_close()).pack(side="left", padx=5)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10),
                  width=8, command=review_win.destroy).pack(side="left", padx=5)

        def _save_and_close():
            active = get_active_positions()
            if not active:
                self.status_text.set("没有选中的位置, 跳过保存")
                review_win.destroy()
                return

            if state["selected_platform_idx"] > 0:
                idx = state["selected_platform_idx"] - 1
                if idx < len(existing_platforms):
                    merged = list(existing_platforms[idx].get("all_points", []))
                else:
                    merged = []
                merged.extend(active)
                all_pts = merged
            else:
                all_pts = list(active)

            all_pts.sort(key=lambda p: (p[0], p[1]))
            unique = []
            for p in all_pts:
                if not unique or p != unique[-1]:
                    unique.append(p)
            all_pts = unique

            platform_data = self._compute_platform_data(all_pts)

            if state["selected_platform_idx"] > 0:
                idx = state["selected_platform_idx"] - 1
                existing_platforms[idx] = platform_data
                self._platform_save(map_name, existing_platforms)
            else:
                existing_platforms.append(platform_data)
                self._platform_save(map_name, existing_platforms)

            review_win.destroy()

        self.root.wait_window(review_win)

    def _compute_platform_data(self, all_points):
        adjusted = [(x, y + self.PLATFORM_Y_OFFSET) for x, y in all_points]
        adjusted.sort(key=lambda p: p[0])

        simplified = rdp_simplify(adjusted, epsilon=self.PLATFORM_RDP_EPSILON)
        if len(simplified) < 2:
            simplified = [adjusted[0], adjusted[-1]]

        left_ep = {"x": simplified[0][0], "y": simplified[0][1]}
        right_ep = {"x": simplified[-1][0], "y": simplified[-1][1]}

        all_y = [p[1] for p in simplified]
        min_y = min(all_y)
        max_y = max(all_y)
        avg_y = sum(all_y) // len(all_y)

        turning_points = []
        for x, y in simplified:
            turning_points.append({"x": x, "y": y, "type": "valley"})

        return {
            "left_endpoint": left_ep,
            "right_endpoint": right_ep,
            "min_y": min_y,
            "max_y": max_y,
            "avg_y": avg_y,
            "turning_points": turning_points,
            "all_points": adjusted,
        }

    def _platform_save(self, map_name, platforms: list):
        if not map_name or not platforms:
            self.status_text.set("无数据, 跳过保存")
            return
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        existing = data.get(map_name, {})
        existing["platforms"] = platforms
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "ropes" not in existing: existing["ropes"] = []
        if "jumps" not in existing: existing["jumps"] = []
        if "flash_points" not in existing: existing["flash_points"] = []
        data[map_name] = existing

        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.status_text.set(
            self.status_text.get() +
            f" | {len(platforms)}个平台已保存至 {MAPS_FILE.name}")

    # ==================== 3. Jump marking ====================

    def _on_jump_toggle(self) -> None:
        if self._mode == "platform":
            self.status_text.set("平台标记运行中，请先停止"); return
        if self._mode == "rope":
            self.status_text.set("绳梯标记运行中，请先停止"); return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止"); return
        if self._mode == "jump":
            self._jump_stop()
        else:
            self._jump_start()

    def _jump_start(self) -> None:
        if not self._check_minimap_ready(): return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get())
        mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get())
        mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb)
        self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"跳跃点标记中... (地图: {map_name})")
        self.jump_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None
        self.frame_count = 0
        self.running = True
        self._mode = "jump"
        self._jump_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="jump")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(target=self._loop_jump, args=(map_name,), daemon=True)
        self.thread.start()

    def _jump_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._jump_button_set_running(False)
        self._set_mode_buttons("normal")
        self.confirm_btn.config(state="normal")
        self._mode = None
        if self.jump_detector.count > 0:
            self._jump_review_and_save()
        else:
            self.status_text.set("跳跃点标记已停止 | 未检测到跳跃")

    def _jump_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["jump"]
        if running:
            btn.config(text="停止跳跃点标记", bg="#ff6b6b", activebackground="#e85a5a")
        else:
            btn.config(text="跳跃点标记", bg="#1abc9c", activebackground="#16a085")

    def _loop_jump(self, map_name: str) -> None:
        import ctypes
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()
        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt: time.sleep(0.1); continue
                ml, mt, mr, mb = self.mm_offsets
                ml_abs: int = gl + ml; mt_abs: int = gt + mt
                mw: int = mr - ml; mh: int = mb - mt
                if mw <= 0 or mh <= 0: time.sleep(0.1); continue
                region: dict = {"left": ml_abs, "top": mt_abs, "width": mw, "height": mh}
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None:
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None: self.jump_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now: float = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"跳跃点标记中... {self.frame_count}帧 | "
                        f"已检测{self.jump_detector.count}次跳跃")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}"); break
            sleep_t: float = interval - (time.time() - t0)
            if sleep_t > 0: time.sleep(sleep_t)
        msg: str = f"已停止  {self.frame_count}帧 | 检测到{self.jump_detector.count}次跳跃"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Jump Review & Save ====================

    def _jump_review_and_save(self) -> None:
        try: self._jump_review_and_save_impl()
        except Exception as e:
            import traceback
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_jump_error.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.status_text.set(f"跳跃审阅窗口错误: {e}")

    def _jump_review_and_save_impl(self) -> None:
        map_name: str = self.map_name_var.get().strip()
        yoff: int = self.jump_detector.y_offset
        new_jumps: list = [[fx, fy + yoff, tx, ty + yoff]
                           for fx, fy, tx, ty in self.jump_detector.jumps]
        old_jumps: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
            old_jumps = data.get(map_name, {}).get("jumps", [])
            if not old_jumps:
                old_jumps = data.get(map_name, {}).get("teleports", [])

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)

        state: dict = {"source": None, "idx": -1}

        rope_items_nj: list = []
        rope_labels_nj: list[str] = []

        def _rebuild() -> None:
            nonlocal rope_items_nj, rope_labels_nj
            rope_items_nj, rope_labels_nj = [], []
            for i, r in enumerate(new_jumps):
                fx, fy, tx, ty = r
                rope_items_nj.append({"source": "new", "idx": i, "data": r})
                rope_labels_nj.append(f"新{i + 1}: ({fx},{fy}) -> ({tx},{ty})")
            for i, r in enumerate(old_jumps):
                frm, to = r["from"], r["to"]
                rope_items_nj.append({"source": "old", "idx": i, "data": r})
                rope_labels_nj.append(f"旧{i + 1}: ({frm['x']},{frm['y']}) -> ({to['x']},{to['y']})")

        _rebuild()

        def draw_preview() -> ImageTk.PhotoImage:
            img: Image.Image = draw_jump_preview(
                self._mm_snapshot, self.mm_size, map_name,
                new_jumps, old_jumps, state["source"], state["idx"],
                target_size=(dw, dh))
            return ImageTk.PhotoImage(img)

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅跳跃点 - {map_name}")
        review_win.transient(self.root); review_win.grab_set()

        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        canvas.photo = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        def refresh_preview() -> None:
            canvas.photo = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)
        tk.Label(right_frame, text=f"跳跃点: {len(rope_items_nj)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(list_frame, font=("Consolas", 9), width=32,
                             height=14, selectmode="single", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        sb2 = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        sb2.pack(side="right", fill="y"); listbox.configure(yscrollcommand=sb2.set)

        def _fill_listbox() -> None:
            listbox.delete(0, "end")
            for label in rope_labels_nj:
                listbox.insert("end", label)
        _fill_listbox()

        def on_select(_event=None) -> None:
            sel = listbox.curselection()
            if sel and sel[0] < len(rope_items_nj):
                it = rope_items_nj[sel[0]]
                state["source"], state["idx"] = it["source"], it["idx"]
            else:
                state["source"], state["idx"] = None, -1
            refresh_preview()
        listbox.bind("<<ListboxSelect>>", on_select)

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=6)
        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=4)

        def delete_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先选择"); return
            idx: int = sel[0]
            if idx >= len(rope_items_nj): return
            it = rope_items_nj[idx]
            if it["source"] == "new": new_jumps.pop(it["idx"])
            else: old_jumps.pop(it["idx"])
            state["source"], state["idx"] = None, -1
            _rebuild(); _fill_listbox(); refresh_preview()

        def edit_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先选择"); return
            idx: int = sel[0]
            if idx >= len(rope_items_nj): return
            it = rope_items_nj[idx]
            ew = tk.Toplevel(review_win)
            ew.title("编辑跳跃点"); ew.transient(review_win); ew.grab_set()
            ew.resizable(False, False)
            if it["source"] == "new": fx, fy, tx, ty = it["data"]
            else: frm, to = it["data"]["from"], it["data"]["to"]; fx, fy, tx, ty = frm["x"], frm["y"], to["x"], to["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="起跳点", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e", padx=(0, 4))
            fx_var = tk.StringVar(value=str(fx)); tk.Entry(f, textvariable=fx_var, width=6, font=("Consolas", 10)).grid(row=1, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e", padx=(0, 4))
            fy_var = tk.StringVar(value=str(fy)); tk.Entry(f, textvariable=fy_var, width=6, font=("Consolas", 10)).grid(row=2, column=1)
            tk.Label(f, text="落脚点", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=4, column=0, sticky="e", padx=(0, 4))
            tx_var = tk.StringVar(value=str(tx)); tk.Entry(f, textvariable=tx_var, width=6, font=("Consolas", 10)).grid(row=4, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=5, column=0, sticky="e", padx=(0, 4))
            ty_var = tk.StringVar(value=str(ty)); tk.Entry(f, textvariable=ty_var, width=6, font=("Consolas", 10)).grid(row=5, column=1)

            def _apply() -> None:
                try: nfx, nfy, ntx, nty = int(fx_var.get()), int(fy_var.get()), int(tx_var.get()), int(ty_var.get())
                except ValueError: self.status_text.set("坐标必须为整数"); return
                if it["source"] == "new": new_jumps[it["idx"]] = [nfx, nfy, ntx, nty]
                else: old_jumps[it["idx"]] = {"from": {"x": nfx, "y": nfy}, "to": {"x": ntx, "y": nty}}
                _rebuild(); _fill_listbox(); refresh_preview(); ew.destroy()

            bf = tk.Frame(f); bf.grid(row=6, column=0, columnspan=2, pady=(12, 0))
            tk.Button(bf, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_apply).pack(side="left", padx=4)
            tk.Button(bf, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

        def save_and_close() -> None:
            all_saved: list = []
            for r in new_jumps:
                fx, fy, tx, ty = r
                all_saved.append({"from": {"x": fx, "y": fy}, "to": {"x": tx, "y": ty}})
            all_saved.extend(old_jumps)
            self._jump_save(map_name, all_saved)
            review_win.destroy()

        tk.Button(btn_frame, text="删除", font=("Microsoft YaHei", 10), width=8, bg="#e74c3c", fg="white", cursor="hand2", command=delete_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="编辑坐标", font=("Microsoft YaHei", 10), width=8, bg="#3498db", fg="white", cursor="hand2", command=edit_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"), width=8, bg="#4ecdc4", fg="white", cursor="hand2", command=save_and_close).pack(side="left", padx=3)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10), width=8, cursor="hand2", command=review_win.destroy).pack(side="left", padx=3)
        self.root.wait_window(review_win)

    def _jump_save(self, map_name: str, jumps: list) -> None:
        if not map_name: return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        existing: dict = data.get(map_name, {})
        existing["jumps"] = jumps
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "platforms" not in existing: existing["platforms"] = []
        if "ropes" not in existing: existing["ropes"] = []
        data[map_name] = existing
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_text.set(f" {len(jumps)}个跳跃点已保存至 {MAPS_FILE.name}")

    # ==================== 4. Flash / teleport marking ====================

    def _on_flash_toggle(self) -> None:
        if self._mode is not None and self._mode != "flash":
            self.status_text.set(f"{self._mode}标记运行中，请先停止"); return
        if self._mode == "flash": self._flash_stop()
        else: self._flash_start()

    def _flash_start(self) -> None:
        if not self._check_minimap_ready(): return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get()); mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get()); mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb); self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"闪现点标记中... (地图: {map_name})")
        self.flash_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None; self.frame_count = 0
        self.running = True; self._mode = "flash"
        self._flash_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="flash")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(target=self._loop_flash, args=(map_name,), daemon=True)
        self.thread.start()

    def _flash_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._flash_button_set_running(False)
        self._set_mode_buttons("normal"); self.confirm_btn.config(state="normal")
        self._mode = None
        if self.flash_detector.count > 0: self._flash_review_and_save()
        else: self.status_text.set("闪现点标记已停止 | 未检测到闪现")

    def _flash_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["flash"]
        if running: btn.config(text="停止闪现点标记", bg="#ff6b6b", activebackground="#e85a5a")
        else: btn.config(text="闪现点标记", bg="#f39c12", activebackground="#e67e22")

    def _loop_flash(self, map_name: str) -> None:
        import ctypes
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()
        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt: time.sleep(0.1); continue
                ml, mt, mr, mb = self.mm_offsets
                if mr - ml <= 0 or mb - mt <= 0: time.sleep(0.1); continue
                region: dict = {"left": gl + ml, "top": gt + mt,
                                "width": mr - ml, "height": mb - mt}
                img_raw = sct.grab(region); mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None: self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None: self.flash_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"闪现点标记中... {self.frame_count}帧 | "
                        f"已检测{self.flash_detector.count}次闪现")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}"); break
            sleep_t = interval - (time.time() - t0)
            if sleep_t > 0: time.sleep(sleep_t)
        msg = f"已停止  {self.frame_count}帧 | 检测到{self.flash_detector.count}次闪现"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Flash Review & Save ====================

    def _flash_review_and_save(self) -> None:
        try: self._flash_review_and_save_impl()
        except Exception as e:
            import traceback
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_flash_error.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.status_text.set(f"闪现审阅窗口错误: {e}")

    def _flash_review_and_save_impl(self) -> None:
        map_name = self.map_name_var.get().strip()
        yoff = self.flash_detector.y_offset
        new_flash = [[fx, fy + yoff, tx, ty + yoff, dr]
                     for fx, fy, tx, ty, dr in self.flash_detector.flashes]
        old_flash = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                old_flash = json.load(f).get(map_name, {}).get("flash_points", [])
        sw, sh = self.mm_size
        scale = min(6.0, 700 / max(sw, sh, 1))
        dw, dh = int(sw * scale), int(sh * scale)
        state: dict = {"source": None, "idx": -1}
        items, labels = [], []

        def _rebuild():
            nonlocal items, labels
            items, labels = [], []
            for i, r in enumerate(new_flash):
                dr = r[4] if len(r) > 4 else "?"
                tp = r[5] if len(r) > 5 else "one_way"
                tp_label = "单向" if tp == "one_way" else "双向"
                items.append({"source": "new", "idx": i, "data": r})
                labels.append(f"新{i+1}: ({r[0]},{r[1]}) -> ({r[2]},{r[3]}) [{dr}][{tp_label}]")
            for i, r in enumerate(old_flash):
                frm, to = r["from"], r["to"]
                tp = r.get("type", "one_way")
                tp_label = "单向" if tp == "one_way" else "双向"
                items.append({"source": "old", "idx": i, "data": r})
                labels.append(f"旧{i+1}: ({frm['x']},{frm['y']}) -> ({to['x']},{to['y']}) [{tp_label}]")
        _rebuild()

        def draw_preview():
            return ImageTk.PhotoImage(draw_flash_preview(
                self._mm_snapshot, self.mm_size, map_name,
                new_flash, old_flash, state["source"], state["idx"],
                target_size=(dw, dh)))

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅闪现点 - {map_name}")
        review_win.transient(self.root); review_win.grab_set()
        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        canvas.photo = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        def refresh_preview():
            canvas.photo = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        right = tk.Frame(review_win)
        right.pack(side="right", padx=10, pady=10, fill="both", expand=True)
        tk.Label(right, text=f"闪现点: {len(items)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        lf = tk.Frame(right); lf.pack(fill="both", expand=True)
        lb = tk.Listbox(lf, font=("Consolas", 9), width=32, height=14,
                        selectmode="single", exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lf, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y"); lb.configure(yscrollcommand=sb.set)

        def _fill(): lb.delete(0, "end"); [lb.insert("end", L) for L in labels]
        _fill()

        def on_sel(_e=None):
            s = lb.curselection()
            if s: it = items[s[0]]; state["source"], state["idx"] = it["source"], it["idx"]
            else: state["source"], state["idx"] = None, -1
            refresh_preview()
        lb.bind("<<ListboxSelect>>", on_sel)

        tk.Frame(right, height=1, bg="#ccc").pack(fill="x", pady=6)
        bf = tk.Frame(right); bf.pack(side="bottom", fill="x", pady=4)

        def del_sel():
            s = lb.curselection()
            if not s: self.status_text.set("请先选择"); return
            it = items[s[0]]
            if it["source"] == "new": new_flash.pop(it["idx"])
            else: old_flash.pop(it["idx"])
            state["source"], state["idx"] = None, -1
            _rebuild(); _fill(); refresh_preview()

        def edit_sel():
            s = lb.curselection()
            if not s: self.status_text.set("请先选择"); return
            it = items[s[0]]
            ew = tk.Toplevel(review_win)
            ew.title("编辑闪现点"); ew.transient(review_win); ew.grab_set(); ew.resizable(False, False)
            if it["source"] == "new": fx, fy, tx, ty, *_ = it["data"]
            else: frm, to = it["data"]["from"], it["data"]["to"]; fx, fy, tx, ty = frm["x"], frm["y"], to["x"], to["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="起始点", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            fx_v = tk.StringVar(value=str(fx)); fy_v = tk.StringVar(value=str(fy))
            tx_v = tk.StringVar(value=str(tx)); ty_v = tk.StringVar(value=str(ty))
            for r, lbl, var in [(1, "X:", fx_v), (2, "Y:", fy_v)]:
                tk.Label(f, text=lbl, font=("Microsoft YaHei", 9)).grid(row=r, column=0, sticky="e", padx=(0, 4))
                tk.Entry(f, textvariable=var, width=6, font=("Consolas", 10)).grid(row=r, column=1)
            tk.Label(f, text="终点", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            for r, lbl, var in [(4, "X:", tx_v), (5, "Y:", ty_v)]:
                tk.Label(f, text=lbl, font=("Microsoft YaHei", 9)).grid(row=r, column=0, sticky="e", padx=(0, 4))
                tk.Entry(f, textvariable=var, width=6, font=("Consolas", 10)).grid(row=r, column=1)
            tp_var = tk.StringVar(value="one_way")
            tp_row = tk.Frame(f); tp_row.grid(row=6, column=0, columnspan=2, pady=(8, 0))
            if it["source"] == "old": tp_var.set(it["data"].get("type", "one_way"))
            tk.Label(tp_row, text="类型:", font=("Microsoft YaHei", 9)).pack(side="left")
            tk.Radiobutton(tp_row, text="单向", variable=tp_var, value="one_way", font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
            tk.Radiobutton(tp_row, text="双向", variable=tp_var, value="two_way", font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

            def _ap():
                try: nf = (int(fx_v.get()), int(fy_v.get()), int(tx_v.get()), int(ty_v.get()))
                except ValueError: self.status_text.set("坐标必须为整数"); return
                if it["source"] == "new":
                    old_dr = it["data"][4] if len(it["data"]) > 4 else "?"
                    new_flash[it["idx"]] = [nf[0], nf[1], nf[2], nf[3], old_dr, tp_var.get()]
                else:
                    old_flash[it["idx"]] = {
                        "from": {"x": nf[0], "y": nf[1]},
                        "to": {"x": nf[2], "y": nf[3]},
                        "type": tp_var.get()}
                _rebuild(); _fill(); refresh_preview(); ew.destroy()

            bf2 = tk.Frame(f); bf2.grid(row=7, column=0, columnspan=2, pady=(12, 0))
            tk.Button(bf2, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_ap).pack(side="left", padx=4)
            tk.Button(bf2, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

        def save_and_close():
            all_s = []
            for r in new_flash:
                tp = r[5] if len(r) > 5 else "one_way"
                all_s.append({"from": {"x": r[0], "y": r[1]}, "to": {"x": r[2], "y": r[3]}, "type": tp})
            all_s.extend(old_flash)
            self._flash_save(map_name, all_s)
            review_win.destroy()

        tk.Button(bf, text="删除", font=("Microsoft YaHei", 10), width=8, bg="#e74c3c", fg="white", cursor="hand2", command=del_sel).pack(side="left", padx=3)
        tk.Button(bf, text="编辑坐标", font=("Microsoft YaHei", 10), width=8, bg="#3498db", fg="white", cursor="hand2", command=edit_sel).pack(side="left", padx=3)
        tk.Button(bf, text="保存", font=("Microsoft YaHei", 10, "bold"), width=8, bg="#4ecdc4", fg="white", cursor="hand2", command=save_and_close).pack(side="left", padx=3)
        tk.Button(bf, text="取消", font=("Microsoft YaHei", 10), width=8, cursor="hand2", command=review_win.destroy).pack(side="left", padx=3)
        self.root.wait_window(review_win)

    def _flash_save(self, map_name, flashes):
        if not map_name: return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        existing = data.get(map_name, {})
        existing["flash_points"] = flashes
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        for k in ("platforms", "ropes", "jumps"):
            if k not in existing: existing[k] = []
        data[map_name] = existing
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_text.set(f" {len(flashes)}个闪现点已保存至 {MAPS_FILE.name}")

    # ==================== 5. Rope ladder marking ====================

    def _on_rope_toggle(self) -> None:
        if self._mode == "platform":
            self.status_text.set("平台标记运行中，请先停止"); return
        if self._mode == "jump":
            self.status_text.set("跳跃点标记运行中，请先停止"); return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止"); return
        if self._mode == "rope": self._rope_stop()
        else: self._rope_start()

    def _rope_start(self) -> None:
        if not self._check_minimap_ready(): return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get()); mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get()); mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb); self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"绳梯标记中... 检测绳梯顶/底 (地图: {map_name})")
        self.rope_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None; self.frame_count = 0
        self.running = True; self._mode = "rope"
        self._rope_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="rope")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(target=self._loop_rope, args=(map_name,), daemon=True)
        self.thread.start()

    def _rope_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._rope_button_set_running(False)
        self._set_mode_buttons("normal"); self.confirm_btn.config(state="normal")
        self._mode = None
        if self.rope_detector.count > 0: self._rope_review_and_save()
        else: self.status_text.set("绳梯标记已停止 | 未检测到任何绳梯")

    def _rope_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["rope"]
        if running: btn.config(text="停止绳梯标记", bg="#ff6b6b", activebackground="#e85a5a")
        else: btn.config(text="绳梯标记", bg="#e67e22", activebackground="#d35400")

    def _loop_rope(self, map_name: str) -> None:
        import ctypes
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()
        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt: time.sleep(0.1); continue
                ml, mt, mr, mb = self.mm_offsets
                ml_abs: int = gl + ml; mt_abs: int = gt + mt
                mw: int = mr - ml; mh: int = mb - mt
                if mw <= 0 or mh <= 0: time.sleep(0.1); continue
                region: dict = {"left": ml_abs, "top": mt_abs, "width": mw, "height": mh}
                img_raw = sct.grab(region); mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None: self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None: self.rope_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now: float = time.time()
                if now - last_status > 0.5:
                    pending_hint: str = " | 等待底部..." if self.rope_detector.has_pending else ""
                    self.root.after(0, self.status_text.set,
                        f"绳梯标记中... {self.frame_count}帧 | "
                        f"已检测{self.rope_detector.count}条绳梯{pending_hint}")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}"); break
            sleep_t: float = interval - (time.time() - t0)
            if sleep_t > 0: time.sleep(sleep_t)
        msg: str = f"已停止  {self.frame_count}帧 | 检测到{self.rope_detector.count}条绳梯"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Rope Review & Save ====================

    def _rope_review_and_save(self) -> None:
        try: self._rope_review_and_save_impl()
        except Exception as e:
            import traceback
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_rope_error.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.status_text.set(f"绳梯审阅窗口错误: {e}")

    def _rope_review_and_save_impl(self) -> None:
        map_name: str = self.map_name_var.get().strip()
        yoff: int = self.rope_detector.y_offset
        new_ropes: list = [[tx, ty + yoff, bx, by + yoff]
                           for tx, ty, bx, by in self.rope_detector.ropes]
        old_ropes: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                old_ropes = json.load(f).get(map_name, {}).get("ropes", [])

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)
        state: dict = {"source": None, "idx": -1}
        rope_items: list = []; rope_labels: list[str] = []

        def _rebuild_items() -> None:
            nonlocal rope_items, rope_labels
            rope_items, rope_labels = [], []
            for i, r in enumerate(new_ropes):
                tx, ty, bx, by = r
                rope_items.append({"source": "new", "idx": i, "data": r})
                rope_labels.append(f"新{i + 1}: ({tx},{ty}) → ({bx},{by})")
            for i, r in enumerate(old_ropes):
                t: dict = r["top"]; b: dict = r["bottom"]
                rope_items.append({"source": "old", "idx": i, "data": r})
                rope_labels.append(f"旧{i + 1}: ({t['x']},{t['y']}) → ({b['x']},{b['y']})")
        _rebuild_items()

        def draw_preview() -> ImageTk.PhotoImage:
            img: Image.Image = draw_rope_preview(
                self._mm_snapshot, self.mm_size, map_name,
                new_ropes, old_ropes, state["source"], state["idx"],
                target_size=(dw, dh))
            return ImageTk.PhotoImage(img)

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅绳梯 - {map_name}")
        review_win.transient(self.root); review_win.grab_set()
        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        canvas.photo = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        def refresh_preview() -> None:
            canvas.photo = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)
        tk.Label(right_frame, text=f"绳梯标记: {len(rope_items)} 条",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(list_frame, font=("Consolas", 9), width=32,
                             height=14, selectmode="single", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scrollbar.pack(side="right", fill="y"); listbox.configure(yscrollcommand=scrollbar.set)

        def _fill_listbox() -> None:
            listbox.delete(0, "end")
            for label in rope_labels: listbox.insert("end", label)
        _fill_listbox()

        def on_listbox_select(_event=None) -> None:
            sel = listbox.curselection()
            if sel and sel[0] < len(rope_items):
                it = rope_items[sel[0]]
                state["source"], state["idx"] = it["source"], it["idx"]
            else: state["source"], state["idx"] = None, -1
            refresh_preview()
        listbox.bind("<<ListboxSelect>>", on_listbox_select)

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=6)
        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=4)

        def delete_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先在列表中选择一条绳梯"); return
            idx: int = sel[0]
            if idx >= len(rope_items): return
            it: dict = rope_items[idx]
            if it["source"] == "new": new_ropes.pop(it["idx"])
            else: old_ropes.pop(it["idx"])
            state["source"], state["idx"] = None, -1
            _rebuild_items(); _fill_listbox(); refresh_preview()

        def save_and_close() -> None:
            all_saved: list = []
            for r in new_ropes:
                tx, ty, bx, by = r
                all_saved.append({"top": {"x": tx, "y": ty}, "bottom": {"x": bx, "y": by}})
            all_saved.extend(old_ropes)
            self._rope_save(map_name, all_saved)
            review_win.destroy()

        def edit_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先在列表中选择一条绳梯"); return
            idx: int = sel[0]
            if idx >= len(rope_items): return
            it: dict = rope_items[idx]
            ew = tk.Toplevel(review_win)
            ew.title("编辑绳梯坐标"); ew.transient(review_win); ew.grab_set(); ew.resizable(False, False)
            if it["source"] == "new": tx, ty, bx, by = it["data"]
            else: t, b = it["data"]["top"], it["data"]["bottom"]; tx, ty, bx, by = t["x"], t["y"], b["x"], b["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="顶部坐标", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e", padx=(0, 4))
            top_x_var = tk.StringVar(value=str(tx)); tk.Entry(f, textvariable=top_x_var, width=6, font=("Consolas", 10)).grid(row=1, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e", padx=(0, 4))
            top_y_var = tk.StringVar(value=str(ty)); tk.Entry(f, textvariable=top_y_var, width=6, font=("Consolas", 10)).grid(row=2, column=1)
            tk.Label(f, text="底部坐标", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=4, column=0, sticky="e", padx=(0, 4))
            bot_x_var = tk.StringVar(value=str(bx)); tk.Entry(f, textvariable=bot_x_var, width=6, font=("Consolas", 10)).grid(row=4, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=5, column=0, sticky="e", padx=(0, 4))
            bot_y_var = tk.StringVar(value=str(by)); tk.Entry(f, textvariable=bot_y_var, width=6, font=("Consolas", 10)).grid(row=5, column=1)

            def _apply_edit() -> None:
                try: ntx, nty, nbx, nby = int(top_x_var.get()), int(top_y_var.get()), int(bot_x_var.get()), int(bot_y_var.get())
                except ValueError: self.status_text.set("坐标必须为整数"); return
                if it["source"] == "new": new_ropes[it["idx"]] = [ntx, nty, nbx, nby]
                else: old_ropes[it["idx"]] = {"top": {"x": ntx, "y": nty}, "bottom": {"x": nbx, "y": nby}}
                _rebuild_items(); _fill_listbox(); refresh_preview(); ew.destroy()

            btn_f = tk.Frame(f); btn_f.grid(row=6, column=0, columnspan=2, pady=(12, 0))
            tk.Button(btn_f, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_apply_edit).pack(side="left", padx=4)
            tk.Button(btn_f, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

        tk.Button(btn_frame, text="删除", font=("Microsoft YaHei", 10), width=8, bg="#e74c3c", fg="white", cursor="hand2", command=delete_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="编辑坐标", font=("Microsoft YaHei", 10), width=8, bg="#3498db", fg="white", cursor="hand2", command=edit_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"), width=8, bg="#4ecdc4", fg="white", cursor="hand2", command=save_and_close).pack(side="left", padx=3)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10), width=8, cursor="hand2", command=review_win.destroy).pack(side="left", padx=3)
        self.root.wait_window(review_win)

    def _rope_save(self, map_name: str, ropes: list) -> None:
        if not map_name: return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        existing: dict = data.get(map_name, {})
        existing["ropes"] = ropes
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "platforms" not in existing: existing["platforms"] = []
        if "jumps" not in existing: existing["jumps"] = []
        if "flash_points" not in existing: existing["flash_points"] = []
        data[map_name] = existing
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_text.set(f" {len(ropes)}条绳梯已保存至 {MAPS_FILE.name}")

    # ==================== 6. View all markers ====================

    def _on_view_markers(self) -> None:
        if self.running: self.status_text.set("标记运行中，请先停止"); return
        map_name: str = self.map_name_var.get().strip()
        if not map_name: self.status_text.set("请先输入地图名称"); return
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
            self.status_text.set(f"地图 '{map_name}' 尚无标记数据"); return
        mm_region = map_cfg.get("mm_region")
        if mm_region and len(mm_region) == 4: mw, mh = mm_region[2] - mm_region[0], mm_region[3] - mm_region[1]
        else:
            mw, mh = self.mm_size
            if mw <= 0 or mh <= 0: mw, mh = 154, 156
        scale: float = min(6.0, 700 / max(mw, mh, 1))
        dw: int = int(mw * scale); dh: int = int(mh * scale)
        img: Image.Image = draw_markers_overview(
            self._mm_snapshot, (mw, mh), map_name,
            platforms, ropes, jumps, flashes, target_size=(dw, dh))
        photo = ImageTk.PhotoImage(img)
        view_win = tk.Toplevel(self.root)
        view_win.title(f"查看标记 - {map_name}")
        view_win.transient(self.root); view_win.grab_set(); view_win.resizable(False, False)
        canvas = tk.Canvas(view_win, width=dw, height=dh, highlightthickness=0)
        canvas.pack(padx=5, pady=5)
        canvas.create_image(0, 0, anchor="nw", image=photo); canvas.photo = photo
        self.root.wait_window(view_win)

    # ==================== 7. Model generate ====================

    def _on_model_generate(self) -> None:
        if self.running: self.status_text.set("标记运行中，请先停止"); return
        map_name: str = self.map_name_var.get().strip()
        if not map_name: self.status_text.set("请先输入地图名称"); return
        if not MAPS_FILE.exists(): self.status_text.set("maps.json 不存在"); return
        with open(MAPS_FILE, "r", encoding="utf-8") as f: data: dict = json.load(f)
        map_cfg: dict = data.get(map_name, {})
        platforms_raw: list = map_cfg.get("platforms", [])
        ropes: list = map_cfg.get("ropes", [])
        jumps: list = map_cfg.get("jumps", [])
        flashes: list = map_cfg.get("flash_points", [])
        if not platforms_raw: self.status_text.set("请先标记平台"); return
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
                _add("rope", pb, pt, direction="up", from_pt={"x": bx, "y": by}, to_pt={"x": tx, "y": ty})
                _add("rope", pt, pb, direction="down", from_pt={"x": tx, "y": ty}, to_pt={"x": bx, "y": by})
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

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)

        mgr = tk.Toplevel(self.root)
        mgr.title(f"地图模型 - {map_name}")
        mgr.transient(self.root); mgr.grab_set()
        left = tk.Frame(mgr); left.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(left, width=dw, height=dh, highlightthickness=0); canvas.pack()
        sel_idx = [-1]
        PLAT_COLOR = (80, 200, 255, 120); PLAT_HI = (255, 220, 80, 200)
        EDGE_COLORS = {"rope": (255, 200, 40), "jump": (180, 80, 220), "flash": (255, 100, 30)}
        HI_COLOR = (255, 50, 50)

        def _draw_map() -> ImageTk.PhotoImage:
            w2, h2 = sw, sh; rx, ry = dw / w2, dh / h2; rt = (rx + ry) / 2
            if self._mm_snapshot and self._mm_snapshot.size == (w2, h2):
                img = self._mm_snapshot.resize((dw, dh), Image.LANCZOS).copy()
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
            output = {"map_name": map_name, "minimap_size": list(self.mm_size),
                      "mm_region": list(self.mm_offsets), "platforms": platforms, "edges": edir}
            out_path = os.path.join(OUTPUT_DIR, f"{map_name}_model.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            self.status_text.set(f"模型已保存: {out_path}")
            mgr.destroy()

        tk.Button(bbar, text="编辑", font=("Microsoft YaHei", 9), width=6, bg="#3498db", fg="white", command=_edit).pack(side="left", padx=2)
        tk.Button(bbar, text="删除", font=("Microsoft YaHei", 9), width=6, bg="#e74c3c", fg="white", command=_del).pack(side="left", padx=2)
        tk.Button(bbar, text="新增", font=("Microsoft YaHei", 9), width=6, bg="#2ecc71", fg="white", command=_add).pack(side="left", padx=2)
        tk.Button(bbar, text="保存模型", font=("Microsoft YaHei", 9, "bold"), width=10, bg="#8e44ad", fg="white", command=_save).pack(side="left", padx=6)
        self.root.wait_window(mgr)

    # ==================== 8. Patrol route editor ====================

    def _on_patrol_route(self) -> None:
        if self.running: self.status_text.set("标记运行中，请先停止"); return
        map_name: str = self.map_name_var.get().strip()
        if not map_name: self.status_text.set("请先输入地图名称"); return
        if not MAPS_FILE.exists(): self.status_text.set("maps.json 不存在，请先标记地图"); return
        with open(MAPS_FILE, "r", encoding="utf-8") as f: data: dict = json.load(f)
        map_cfg: dict = data.get(map_name, {})
        platforms_raw: list = map_cfg.get("platforms", [])
        ropes: list = map_cfg.get("ropes", [])
        jumps: list = map_cfg.get("jumps", [])
        flashes: list = map_cfg.get("flash_points", [])
        if not platforms_raw: self.status_text.set("请先生成世界模型（需先标记平台）"); return
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
            raw = sr.get("waypoints", sr.get("segments", []))
            if raw and isinstance(raw[0], dict):
                saved_waypoints: list[str] = []
                for i, s in enumerate(raw):
                    if i == 0: saved_waypoints.append(s.get("start_anchor", s.get("start", "")))
                    saved_waypoints.append(s.get("end_anchor", s.get("end", "")))
            else: saved_waypoints = raw
        else: saved_waypoints: list[str] = []; saved_name = "默认巡逻路线"

        sw, sh = self.mm_size
        scale: float = min(5.0, 600 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)
        win = tk.Toplevel(self.root)
        win.title(f"巡逻路线编辑器 - {map_name}")
        win.transient(self.root); win.grab_set()
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
            if self._mm_snapshot and self._mm_snapshot.size == (w2, h2):
                img = self._mm_snapshot.resize((dw, dh), Image.LANCZOS).copy()
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

        bbar_bot = tk.Frame(right); bbar_bot.pack(fill="x", pady=(6, 0))

        def _save() -> None:
            _sync_waypoints()
            with open(MAPS_FILE, "r", encoding="utf-8") as f: all_data: dict = json.load(f)
            route_data = {"route_name": name_var.get().strip() or "默认巡逻路线", "waypoints": list(waypoints)}
            all_data.setdefault(map_name, {})["patrol_routes"] = [route_data]
            with open(MAPS_FILE, "w", encoding="utf-8") as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
            self.status_text.set(f"巡逻路线已保存到 {map_name}")
            win.destroy()

        tk.Button(bbar_bot, text="保存路线", font=("Microsoft YaHei", 10, "bold"),
                  width=12, bg="#8e44ad", fg="white", command=_save, cursor="hand2").pack(side="left", padx=2)
        tk.Button(bbar_bot, text="取消", font=("Microsoft YaHei", 10),
                  width=8, bg="#95a5a6", fg="white", command=win.destroy, cursor="hand2").pack(side="left", padx=2)
        self.root.wait_window(win)
