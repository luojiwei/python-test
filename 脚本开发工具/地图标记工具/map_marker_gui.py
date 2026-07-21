#!/usr/bin/env python3

import ctypes
import ctypes.wintypes
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

# ==================== Config ====================

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "marker_output"
MAPS_FILE = OUTPUT_DIR / "maps.json"
WINDOW_TITLE = "WingsMs"
CAPTURE_FPS = 20


# ==================== Window API ====================

def enum_visible_windows(min_w=80, min_h=20):
    found = []
    def cb(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        if (r.right - r.left) < min_w or (r.bottom - r.top) < min_h:
            return True
        title = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
        if not title.value.strip():
            return True
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title.value, r.left, r.top, r.right, r.bottom, pid.value))
        return True
    WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WEP(cb), 0)
    found.sort(key=lambda x: (x[5]-x[3])*(x[4]-x[2]), reverse=True)
    return found


def force_foreground(hwnd):
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.c_ulong())
    att = False
    if cur != tgt:
        ctypes.windll.user32.AttachThreadInput(cur, tgt, True)
        att = True
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)
    finally:
        if att:
            ctypes.windll.user32.AttachThreadInput(cur, tgt, False)


def find_window_by_title(title):
    if not title:
        return None
    title_lower = title.lower()
    matches = []
    for hwnd, wt, l, t, r, b, pid in enum_visible_windows(min_w=80, min_h=20):
        if title_lower in wt.lower():
            matches.append((hwnd, wt, l, t, r, b, pid))
    if not matches:
        return None
    matches.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    hwnd, wt, l, t, r, b, _ = matches[0]
    return (hwnd, wt, l, t, r, b)


# ==================== Player Dot Detection ====================

DOT_HSV_LOWER = np.array([18, 100, 150])   # H 放宽到 18, V 放宽到 150
DOT_HSV_UPPER = np.array([40, 255, 255])   # H 上限从 35 扩到 40


def _find_yellow_candidates(minimap_bgr):
    """检测玩家光点。
    关键特征: R+G 总亮度 >= 450 (光点≈510, 草地≈340-400, 区分明显)
    同时要求: 黄色调 (B<R, B<G), 中小面积 (3-80px)
    """
    bgr = minimap_bgr.astype(np.int32)
    R = bgr[:, :, 2]
    G = bgr[:, :, 1]
    B = bgr[:, :, 0]
    # 核心过滤: 亮度足够, 且为黄色调
    mask = ((R + G) >= 450) & (R > B) & (G > B) & (R >= 180) & (G >= 180)
    if mask.sum() < 3:
        return []
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    if num_labels <= 1:
        return []
    cands = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if 3 <= area <= 80:
            cx, cy = centroids[i]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            aspect = max(w, h) / max(1, min(w, h))
            if aspect <= 2.0:
                cands.append((int(cx), int(cy), area, float(R[int(cy), int(cx)] + G[int(cy), int(cx)])))
    return cands


class PlayerTracker:
    def __init__(self, max_dist=15, decay_per_sec=0.15, stationary_cap=5.0):
        self.max_dist = max_dist
        self.decay_per_sec = decay_per_sec
        self.stationary_cap = stationary_cap
        self.tracks = {}
        self.last_t = time.time()

    def update(self, candidates):
        now = time.time()
        dt = now - self.last_t
        self.last_t = now
        for k in list(self.tracks.keys()):
            self.tracks[k]["count"] -= self.decay_per_sec * dt
            if self.tracks[k]["count"] <= 0:
                del self.tracks[k]
        for cx, cy, area, sv in candidates:
            best_key = None
            best_dist = self.max_dist
            for k in list(self.tracks.keys()):
                d = ((cx - k[0]) ** 2 + (cy - k[1]) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_key = k
            if best_key is not None:
                ox, oy = best_key
                vx, vy = cx - ox, cy - oy
                speed = (vx * vx + vy * vy) ** 0.5
                prev_sv = self.tracks[best_key].get("sv_score", sv)
                new_sv = (prev_sv + sv) / 2
                new_count = self.tracks[best_key]["count"] + 1.0
                if speed < 1.5:
                    new_count = min(new_count, self.stationary_cap)
                self.tracks.pop(best_key)
                self.tracks[(cx, cy)] = {"count": new_count, "speed": speed, "sv_score": new_sv}
            else:
                self.tracks[(cx, cy)] = {"count": 1.0, "speed": 0.0, "sv_score": sv}
        if not self.tracks:
            return None
        def score(k):
            t = self.tracks[k]
            return t["count"] + t["speed"] * 0.5 + (t.get("sv_score", 0) / 100000)
        best = max(self.tracks.keys(), key=score)
        if self.tracks[best]["count"] >= 0.5:
            return best
        return None


def detect_player_dot(minimap_bgr, tracker):
    cands = _find_yellow_candidates(minimap_bgr)
    return tracker.update(cands)


# ==================== RDP Simplify ====================

def rdp_simplify(points, epsilon=2.5):
    """Ramer-Douglas-Peucker polyline simplification.
    points: [(x, y), ...] ordered by x
    Returns simplified point list.
    """
    if len(points) < 3:
        return [list(p) for p in points]

    def perp_dist_sq(p, a, b):
        if a[0] == b[0] and a[1] == b[1]:
            dx, dy = p[0] - a[0], p[1] - a[1]
            return dx * dx + dy * dy
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        norm_sq = dx * dx + dy * dy
        cross = abs(dy * (a[0] - p[0]) - (a[1] - p[1]) * dx)
        return (cross * cross) / norm_sq

    epsilon_sq = epsilon * epsilon

    def rdp(pts):
        if len(pts) < 3:
            return list(pts)
        dmax = 0.0
        idx = 0
        for i in range(1, len(pts) - 1):
            d = perp_dist_sq(pts[i], pts[0], pts[-1])
            if d > dmax:
                dmax = d
                idx = i
        if dmax > epsilon_sq:
            left = rdp(pts[:idx + 1])
            right = rdp(pts[idx:])
            return left + right[1:]
        else:
            return [pts[0], pts[-1]]

    return rdp([(p[0], p[1]) for p in points])


# ==================== PlatformRecorder ====================

class PlatformRecorder:
    """Simple position recorder for platform marking — collects (x,y), deduplicates near-dupes."""

    def __init__(self, y_offset=2):
        self._positions: list = []  # [(x, y), ...]
        self.y_offset: int = y_offset

    def add(self, x, y):
        ix, iy = int(x), int(y)
        # Skip if last point is within 1px in both axes
        if self._positions:
            px, py = self._positions[-1]
            if abs(ix - px) <= 1 and abs(iy - py) <= 1:
                return
        self._positions.append((ix, iy))

    def get_positions(self):
        return self._positions.copy()

    @property
    def count(self):
        return len(self._positions)

    def reset(self):
        self._positions = []


# ==================== RopeDetector ====================

class RopeDetector:
    """Detects rope ladder top/bottom from character position sequence.

    Detection rules (using a sliding window of the last 10 recorded positions):
    - TOP:    first 9 y-values are stable (range <= threshold), the 10th y INCREASES
              (character starts climbing down → y goes UP in minimap coordinate)
              → record the 9th position as the rope ladder top.
    - BOTTOM: all 10 y-values are non-decreasing (tolerance applied), and the jump
              from position 9 to 10 is suddenly large (>= big_drop_ratio × avg jump)
              → record the 9th position as the rope ladder bottom.

    Once a top is detected the detector enters "pending" state and waits for a
    matching bottom.  Each completed pair is stored in `self.ropes`.
    """

    def __init__(self, buffer_size: int = 10, stable_threshold: int = 2,
                 big_drop_ratio: float = 2.0, y_offset: int = 3):
        self.buffer: list = []             # sliding window of (x,y)
        self.buffer_size: int = buffer_size
        self.stable_threshold: int = stable_threshold
        self.big_drop_ratio: float = big_drop_ratio
        self.y_offset: int = y_offset      # px, shift y down to match real rope position
        self.ropes: list = []              # completed: [(tx,ty,bx,by), ...]
        self._pending_top = None           # (x,y) waiting for a matching bottom

    def add(self, x: int, y: int) -> str | None:
        """Feed a new position.  Returns 'top', 'bottom', or None."""
        self.buffer.append((int(x), int(y)))
        if len(self.buffer) > self.buffer_size:
            self.buffer.pop(0)
        if len(self.buffer) < self.buffer_size:
            return None

        y_vals = [p[1] for p in self.buffer]

        if self._pending_top is None:
            # --- looking for rope top ---
            # Character stands still (first 9 y stable), then starts climbing
            # DOWN the rope → y INCREASES (goes up in minimap = down in world).
            first_9 = y_vals[:9]
            y_range = max(first_9) - min(first_9)
            if y_range <= self.stable_threshold and y_vals[9] > max(first_9):
                self._pending_top = self.buffer[8]  # 9th position (0-indexed)
                self.buffer = []
                return "top"
        else:
            # --- looking for rope bottom ---
            # While climbing down, y keeps increasing (non-decreasing).
            # At the bottom the character steps off → a sudden large y-jump.
            non_dec = all(
                y_vals[i] <= y_vals[i + 1] + self.stable_threshold
                for i in range(9)
            )
            if non_dec:
                increases = [max(0, y_vals[i + 1] - y_vals[i]) for i in range(9)]
                # average of the first 8 increases (excluding the suspicious last)
                pos_incs = [inc for inc in increases[:8] if inc > 0]
                avg_inc = sum(pos_incs) / len(pos_incs) if pos_incs else 1.0
                last_inc = increases[8]
                if last_inc >= avg_inc * self.big_drop_ratio and last_inc >= 2:
                    bottom = self.buffer[8]
                    self.ropes.append((
                        self._pending_top[0], self._pending_top[1],
                        bottom[0], bottom[1],
                    ))
                    self._pending_top = None
                    self.buffer = []
                    return "bottom"

        return None

    def cancel_pending(self) -> None:
        """Discard a half-detected rope (pending top without bottom)."""
        self._pending_top = None
        self.buffer = []

    def reset(self) -> None:
        """Full reset: clears buffer, pending top and all completed ropes."""
        self.buffer = []
        self.ropes = []
        self._pending_top = None

    @property
    def count(self) -> int:
        return len(self.ropes)

    @property
    def has_pending(self) -> bool:
        """Whether a top has been detected and we are waiting for its bottom."""
        return self._pending_top is not None


# ==================== JumpDetector ====================

class JumpDetector:
    """Detects platform-to-platform jumps from character position sequence.

    Detection (sliding window of last 10 positions):
    Look for a split point where:
      - 3+ positions before the split are stable (y range <= stable_threshold)
      - 3+ positions after  the split are stable
      - The average y before vs after differs by > jump_threshold

    This catches the moment a character leaves one platform and lands on
    another.  The takeoff is the last stable point before the jump,
    the landing is the first stable point after.
    """

    def __init__(self, buffer_size: int = 10, stable_threshold: int = 2,
                 jump_threshold: int = 3, cooldown_frames: int = 15,
                 y_offset: int = 3):
        self.buffer: list = []                 # sliding window
        self.buffer_size: int = buffer_size
        self.stable_threshold: int = stable_threshold
        self.jump_threshold: int = jump_threshold
        self.cooldown_frames: int = cooldown_frames
        self.y_offset: int = y_offset          # px, shift y down to match real position
        self.jumps: list = []   # [(fx,fy,tx,ty,direction), ...]
        self._cooldown: int = 0

    def add(self, x: int, y: int) -> str | None:
        """Feed a new position.  Returns 'jump' or None."""
        if self._cooldown > 0:
            self._cooldown -= 1
            self.buffer.append((int(x), int(y)))
            if len(self.buffer) > self.buffer_size:
                self.buffer.pop(0)
            return None

        self.buffer.append((int(x), int(y)))
        if len(self.buffer) > self.buffer_size:
            self.buffer.pop(0)
        if len(self.buffer) < self.buffer_size:
            return None

        y_vals = [p[1] for p in self.buffer]

        # Try each split point -- at least 3 stable before AND 3 stable after
        for i in range(3, self.buffer_size - 2):
            before = y_vals[:i]
            after = y_vals[i:]

            if max(before) - min(before) > self.stable_threshold:
                continue
            if max(after) - min(after) > self.stable_threshold:
                continue

            avg_before = sum(before) / len(before)
            avg_after = sum(after) / len(after)
            if abs(avg_after - avg_before) > self.jump_threshold:
                # Take the middle of each stable region, not the edge.
                # This avoids picking a frame that is still mid-transition.
                takeoff_mid = i // 2                   # middle of "before"
                landing_mid = i + max(1, (len(after) - 1) // 2)  # middle of "after"
                takeoff = self.buffer[max(0, takeoff_mid)]
                landing = self.buffer[min(len(self.buffer) - 1, landing_mid)]
                self.jumps.append((
                    takeoff[0], takeoff[1], landing[0], landing[1],
                ))
                self.buffer = []
                self._cooldown = self.cooldown_frames
                return "jump"

        return None

    def reset(self) -> None:
        self.buffer = []
        self.jumps = []
        self._cooldown = 0

    @property
    def count(self) -> int:
        return len(self.jumps)


# ==================== FlashDetector ====================

class FlashDetector:
    """Detects flash / teleport from single-frame large displacement (>10 px).

    Unlike jumps (which look for stable→jump→stable patterns), a flash is
    an instant position change in one frame.  Record the pre-flash and
    post-flash positions as a pair.
    """

    def __init__(self, flash_threshold: int = 10, cooldown_frames: int = 20,
                 y_offset: int = 3):
        self.flash_threshold: int = flash_threshold
        self.cooldown_frames: int = cooldown_frames
        self.y_offset: int = y_offset
        self.flashes: list = []      # [(fx,fy,tx,ty,direction), ...]
        self._cooldown: int = 0
        self._prev = None            # (x, y) or None

    def add(self, x: int, y: int) -> str | None:
        """Feed a new position.  Returns 'flash' or None."""
        curr = (int(x), int(y))
        if self._cooldown > 0:
            self._cooldown -= 1
            self._prev = curr
            return None

        if self._prev is not None:
            dx = curr[0] - self._prev[0]
            dy = curr[1] - self._prev[1]
            if (dx * dx + dy * dy) > (self.flash_threshold ** 2):
                direction = "up" if curr[1] < self._prev[1] else "down"
                self.flashes.append(
                    (self._prev[0], self._prev[1], curr[0], curr[1], direction))
                self._cooldown = self.cooldown_frames
                self._prev = curr
                return "flash"

        self._prev = curr
        return None

    def reset(self) -> None:
        self.flashes = []
        self._cooldown = 0
        self._prev = None

    @property
    def count(self) -> int:
        return len(self.flashes)


# ==================== Map Marker App ====================

class MapMarkerApp:
    """统一标记工具: 小地图标记 / 平台标记 / 绳梯标记 / 跳跃点标记 / 闪现点标记"""

    MODES = ("minimap", "platform", "rope", "jump", "flash")

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
        root.geometry("440x520")
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

    # ---- Mode button helpers ----

    def _set_mode_buttons(self, state, except_key=None):
        for key, btn in self.mode_buttons.items():
            if key == except_key:
                continue
            btn.config(state=state)



    # ---- Map name confirm / change ----

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
        """清空小地图坐标输入框和内部状态。"""
        self.mm_left_var.set("")
        self.mm_top_var.set("")
        self.mm_right_var.set("")
        self.mm_bottom_var.set("")
        self.mm_offsets = (0, 0, 0, 0)
        self.mm_size = (0, 0)

    def _on_map_confirm(self):
        """点击确定/更改按钮：确定→查找配置+锁定输入；更改→解锁输入。"""
        if self.running:
            return

        if not self.map_confirmed:
            # ---- 确定模式 ----
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
                if ropes:
                    parts.append(f"{len(ropes)}条绳梯")
                if platforms:
                    parts.append(f"{len(platforms)}平台")
                if jumps:
                    parts.append(f"{len(jumps)}跳跃点")
                if flash_points:
                    parts.append(f"{len(flash_points)}闪现点")
                if len(parts) == 1:
                    parts.append("尚无标记数据")
                if not self.minimap_marked:
                    parts.append("需先标记小地图")
                self.status_text.set(" | ".join(parts))

            self.map_confirmed = True
            self.map_name_entry.config(state="disabled")
            self.confirm_btn.config(text="更改")
        else:
            # ---- 更改模式 ----
            self.map_confirmed = False
            self.minimap_marked = False
            self._clear_mm_coords()
            self.map_name_entry.config(state="normal")
            self.confirm_btn.config(text="确定")
            self.status_text.set("请输入地图名称并点击确定")

    def _check_minimap_ready(self) -> bool:
        """功能按钮点击前的统一检查：游戏窗口 + 地图已确认 + 小地图已标记。"""
        if not self.map_confirmed:
            self.status_text.set("请先输入地图名称并点击确定")
            return False
        if not self.minimap_marked:
            self.status_text.set("请先标记小地图")
            return False
        # Auto-find game window if not already selected
        if self.target_hwnd is None:
            win = find_window_by_title(WINDOW_TITLE)
            if win is None:
                self.status_text.set(f"未找到游戏窗口 '{WINDOW_TITLE}'，请先打开游戏")
                return False
            hwnd, title, gl, gt, gr, gb = win
            self.target_hwnd = hwnd
            self.lbl_window.config(text=f"游戏: {title[:40]}  ({gr-gl}x{gb-gt})")
        return True

    # ---- 1. Minimap marking ----

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

        sct = mss.mss()
        region = {"left": max(0, gl), "top": max(0, gt),
                  "width": gr - gl, "height": gb - gt}
        img = np.array(sct.grab(region))[:, :, :3]

        # 放大显示: 目标最大边 1600px, 至少 1.2 倍(原 0.6 的 2x)
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


    # ---- 3. Platform marking ----

    PLATFORM_Y_OFFSET = 2
    PLATFORM_RDP_EPSILON = 2.5

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

    # ---- Platform Review & Save ----

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

        # Load existing platforms for this map
        existing_platforms = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing_platforms = data.get(map_name, {}).get("platforms", [])

        sw, sh = self.mm_size
        scale = min(6.0, 700 / max(sw, sh, 1))
        dw = int(sw * scale)
        dh = int(sh * scale)

        # Shared state for closures
        state = {
            "selected_platform_idx": 0,  # 0 = new, 1+ = existing
            "preview_img": None,
            "delete_list": [],
        }

        # Build check_vars: one BooleanVar per position, all True initially
        check_vars = [tk.BooleanVar(value=True) for _ in range(len(positions))]

        def get_active_positions():
            return [positions[i] for i, v in enumerate(check_vars) if v.get()]

        def get_preview_positions_and_active():
            """Return (all_positions_for_display, active_set).
            All positions = ALL current positions + existing platform all_points if selected.
            Active set = checked positions only (green dots, others red)."""
            active = get_active_positions()
            active_set = set(active)
            all_pts = list(positions)  # include ALL current positions
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
            img = self._draw_platform_preview(map_name, all_pts,
                target_size=(dw, dh), active_set=active_set)
            state["preview_img"] = ImageTk.PhotoImage(img)
            return state["preview_img"]

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅平台 - {map_name}")
        review_win.transient(self.root)
        review_win.grab_set()

        # Left: minimap preview
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

        # Right panel
        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)

        tk.Label(right_frame, text=f"记录位置: {len(positions)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))

        # Position list with checkboxes (scrollable)
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

        # Separator
        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=8)

        # Platform selection radio
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

        # Buttons
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

            # Merge with existing all_points if needed
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

            # Sort and deduplicate
            all_pts.sort(key=lambda p: (p[0], p[1]))
            unique = []
            for p in all_pts:
                if not unique or p != unique[-1]:
                    unique.append(p)
            all_pts = unique

            platform_data = self._compute_platform_data(all_pts)

            if state["selected_platform_idx"] > 0:
                # Replace existing platform
                idx = state["selected_platform_idx"] - 1
                existing_platforms[idx] = platform_data
                self._platform_save(map_name, existing_platforms)
            else:
                # Append new platform
                existing_platforms.append(platform_data)
                self._platform_save(map_name, existing_platforms)

            review_win.destroy()

        self.root.wait_window(review_win)

    def _draw_platform_preview(self, map_name, positions, target_size=None, active_set=None):
        """active_set: set of (x,y) tuples for green dots; others drawn red."""
        w, h = self.mm_size
        if w <= 0 or h <= 0:
            w, h = 154, 156

        if target_size is not None:
            canvas_w, canvas_h = target_size
        else:
            canvas_w, canvas_h = w, h
        ratio_x = canvas_w / w
        ratio_y = canvas_h / h
        ratio = (ratio_x + ratio_y) / 2

        if self._mm_snapshot is not None and self._mm_snapshot.size == (w, h):
            bg = self._mm_snapshot.resize((canvas_w, canvas_h), Image.LANCZOS)
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
                simplified = rdp_simplify(active_list, epsilon=self.PLATFORM_RDP_EPSILON)
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

    def _compute_platform_data(self, all_points):
        """From position list, compute platform endpoints, turning points, etc."""
        # Apply Y offset
        adjusted = [(x, y + self.PLATFORM_Y_OFFSET) for x, y in all_points]
        adjusted.sort(key=lambda p: p[0])

        # RDP simplify
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
        """Save platforms list to maps.json."""
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
        if "ropes" not in existing:
            existing["ropes"] = []
        if "jumps" not in existing:
            existing["jumps"] = []
        if "flash_points" not in existing:
            existing["flash_points"] = []
        data[map_name] = existing

        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.status_text.set(
            self.status_text.get() +
            f" | {len(platforms)}个平台已保存至 {MAPS_FILE.name}")

    # ---- 4. Teleport marking (placeholder) ----

    # ---- 4. Jump marking ----

    def _on_jump_toggle(self) -> None:
        if self._mode == "platform":
            self.status_text.set("平台标记运行中，请先停止")
            return
        if self._mode == "rope":
            self.status_text.set("绳梯标记运行中，请先停止")
            return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止")
            return
        if self._mode == "jump":
            self._jump_stop()
        else:
            self._jump_start()

    def _jump_start(self) -> None:
        if not self._check_minimap_ready():
            return
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
        self.thread = threading.Thread(
            target=self._loop_jump, args=(map_name,), daemon=True)
        self.thread.start()

    def _jump_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
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
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()

        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(
                    self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt:
                    time.sleep(0.1)
                    continue

                ml, mt, mr, mb = self.mm_offsets
                ml_abs: int = gl + ml
                mt_abs: int = gt + mt
                mw: int = mr - ml
                mh: int = mb - mt

                if mw <= 0 or mh <= 0:
                    time.sleep(0.1)
                    continue

                region: dict = {"left": ml_abs, "top": mt_abs,
                                "width": mw, "height": mh}
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]

                if self._mm_snapshot is None:
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])

                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None:
                    self.jump_detector.add(pos[0], pos[1])

                self.frame_count += 1

                now: float = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"跳跃点标记中... {self.frame_count}帧 | "
                        f"已检测{self.jump_detector.count}次跳跃")
                    last_status = now

            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}")
                break

            sleep_t: float = interval - (time.time() - t0)
            if sleep_t > 0:
                time.sleep(sleep_t)

        msg: str = (f"已停止  {self.frame_count}帧 | "
                    f"检测到{self.jump_detector.count}次跳跃")
        self.root.after(0, self.status_text.set, msg)

    # ---- Jump Review & Save ----

    def _jump_review_and_save(self) -> None:
        try:
            self._jump_review_and_save_impl()
        except Exception as e:
            import traceback
            err: str = traceback.format_exc()
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_jump_error.log", "w", encoding="utf-8") as f:
                f.write(err)
            self.status_text.set(f"跳跃审阅窗口错误: {e}")

    def _jump_review_and_save_impl(self) -> None:
        map_name: str = self.map_name_var.get().strip()
        # Apply y_offset at load — same compensation as rope marking
        yoff: int = self.jump_detector.y_offset
        new_jumps: list = [[fx, fy + yoff, tx, ty + yoff]
                           for fx, fy, tx, ty in self.jump_detector.jumps]

        old_jumps: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
            old_jumps = data.get(map_name, {}).get("jumps", [])
            # 兼容旧字段名
            if not old_jumps:
                old_jumps = data.get(map_name, {}).get("teleports", [])

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale)
        dh: int = int(sh * scale)

        state: dict = {"source": None, "idx": -1}

        rope_items_nj: list = []
        rope_labels_nj: list[str] = []

        def _rebuild() -> None:
            nonlocal rope_items_nj, rope_labels_nj
            rope_items_nj = []
            rope_labels_nj = []
            for i, r in enumerate(new_jumps):
                fx, fy, tx, ty = r
                rope_items_nj.append({"source": "new", "idx": i, "data": r})
                rope_labels_nj.append(
                    f"新{i + 1}: ({fx},{fy}) -> ({tx},{ty})")
            for i, r in enumerate(old_jumps):
                frm = r["from"]
                to = r["to"]
                rope_items_nj.append({"source": "old", "idx": i, "data": r})
                rope_labels_nj.append(
                    f"旧{i + 1}: ({frm['x']},{frm['y']}) -> ({to['x']},{to['y']})")

        _rebuild()

        def draw_preview() -> ImageTk.PhotoImage:
            img: Image.Image = self._draw_jump_preview(
                map_name, new_jumps, old_jumps,
                state["source"], state["idx"],
                target_size=(dw, dh))
            return ImageTk.PhotoImage(img)

        # ======== Window ========
        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅跳跃点 - {map_name}")
        review_win.transient(self.root)
        review_win.grab_set()

        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        initial_img = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=initial_img)
        canvas.photo = initial_img

        def refresh_preview() -> None:
            new_img = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=new_img)
            canvas.photo = new_img

        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)

        tk.Label(right_frame, text=f"跳跃点: {len(rope_items_nj)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))

        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(list_frame, font=("Consolas", 9), width=32,
                             height=14, selectmode="single", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical",
                                 command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scrollbar.set)

        def _fill_listbox() -> None:
            listbox.delete(0, "end")
            for label in rope_labels_nj:
                listbox.insert("end", label)

        _fill_listbox()

        def on_select(_event=None) -> None:
            sel = listbox.curselection()
            if sel:
                idx: int = sel[0]
                if idx < len(rope_items_nj):
                    item = rope_items_nj[idx]
                    state["source"] = item["source"]
                    state["idx"] = item["idx"]
            else:
                state["source"] = None
                state["idx"] = -1
            refresh_preview()

        listbox.bind("<<ListboxSelect>>", on_select)

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=6)

        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=4)

        def delete_selected() -> None:
            sel = listbox.curselection()
            if not sel:
                self.status_text.set("请先选择")
                return
            idx: int = sel[0]
            if idx >= len(rope_items_nj):
                return
            item = rope_items_nj[idx]
            if item["source"] == "new":
                new_jumps.pop(item["idx"])
            else:
                old_jumps.pop(item["idx"])
            state["source"] = None
            state["idx"] = -1
            _rebuild()
            _fill_listbox()
            refresh_preview()

        def edit_selected() -> None:
            sel = listbox.curselection()
            if not sel:
                self.status_text.set("请先选择")
                return
            idx: int = sel[0]
            if idx >= len(rope_items_nj):
                return
            item = rope_items_nj[idx]

            edit_win = tk.Toplevel(review_win)
            edit_win.title("编辑跳跃点")
            edit_win.transient(review_win)
            edit_win.grab_set()
            edit_win.resizable(False, False)

            if item["source"] == "new":
                fx, fy, tx, ty, dr = item["data"]
            else:
                frm = item["data"]["from"]
                to = item["data"]["to"]
                fx, fy, tx, ty = frm["x"], frm["y"], to["x"], to["y"]

            f = tk.Frame(edit_win, padx=15, pady=12)
            f.pack()

            tk.Label(f, text="起跳点", font=("Microsoft YaHei", 10, "bold")
                     ).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(
                row=1, column=0, sticky="e", padx=(0, 4))
            fx_var = tk.StringVar(value=str(fx))
            tk.Entry(f, textvariable=fx_var, width=6,
                     font=("Consolas", 10)).grid(row=1, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(
                row=2, column=0, sticky="e", padx=(0, 4))
            fy_var = tk.StringVar(value=str(fy))
            tk.Entry(f, textvariable=fy_var, width=6,
                     font=("Consolas", 10)).grid(row=2, column=1)

            tk.Label(f, text="落脚点", font=("Microsoft YaHei", 10, "bold")
                     ).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(
                row=4, column=0, sticky="e", padx=(0, 4))
            tx_var = tk.StringVar(value=str(tx))
            tk.Entry(f, textvariable=tx_var, width=6,
                     font=("Consolas", 10)).grid(row=4, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(
                row=5, column=0, sticky="e", padx=(0, 4))
            ty_var = tk.StringVar(value=str(ty))
            tk.Entry(f, textvariable=ty_var, width=6,
                     font=("Consolas", 10)).grid(row=5, column=1)

            def _apply() -> None:
                try:
                    nfx, nfy = int(fx_var.get()), int(fy_var.get())
                    ntx, nty = int(tx_var.get()), int(ty_var.get())
                except ValueError:
                    self.status_text.set("坐标必须为整数")
                    return
                if item["source"] == "new":
                    new_jumps[item["idx"]] = [nfx, nfy, ntx, nty]
                else:
                    old_jumps[item["idx"]] = {
                        "from": {"x": nfx, "y": nfy},
                        "to": {"x": ntx, "y": nty},
                    }
                _rebuild()
                _fill_listbox()
                refresh_preview()
                edit_win.destroy()

            btn_f = tk.Frame(f)
            btn_f.grid(row=6, column=0, columnspan=2, pady=(12, 0))
            tk.Button(btn_f, text="确定", font=("Microsoft YaHei", 9, "bold"),
                      width=6, bg="#4ecdc4", fg="white",
                      command=_apply).pack(side="left", padx=4)
            tk.Button(btn_f, text="取消", font=("Microsoft YaHei", 9),
                      width=6, command=edit_win.destroy).pack(side="left", padx=4)

        def save_and_close() -> None:
            all_saved: list = []
            for r in new_jumps:
                fx, fy, tx, ty = r
                all_saved.append({
                    "from": {"x": fx, "y": fy},
                    "to": {"x": tx, "y": ty},
                })
            all_saved.extend(old_jumps)
            self._jump_save(map_name, all_saved)
            review_win.destroy()

        tk.Button(btn_frame, text="删除", font=("Microsoft YaHei", 10),
                  width=8, bg="#e74c3c", fg="white", cursor="hand2",
                  command=delete_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="编辑坐标", font=("Microsoft YaHei", 10),
                  width=8, bg="#3498db", fg="white", cursor="hand2",
                  command=edit_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"),
                  width=8, bg="#4ecdc4", fg="white", cursor="hand2",
                  command=save_and_close).pack(side="left", padx=3)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10),
                  width=8, cursor="hand2",
                  command=review_win.destroy).pack(side="left", padx=3)

        self.root.wait_window(review_win)

    def _draw_jump_preview(self, map_name: str,
                           new_jumps: list, old_jumps: list,
                           selected_source, selected_idx: int,
                           target_size=None) -> Image.Image:
        """Draw minimap with jump arrows (from → to).

        Colors:
          - New jumps:    cyan arrow
          - Old jumps:    blue arrow
          - Selected:     red arrow (thicker)
        """
        w, h = self.mm_size
        if w <= 0 or h <= 0:
            w, h = 154, 156

        if target_size is not None:
            cw, ch = target_size
        else:
            cw, ch = w, h
        rx: float = cw / w
        ry: float = ch / h
        ratio: float = (rx + ry) / 2

        if self._mm_snapshot is not None and self._mm_snapshot.size == (w, h):
            bg = self._mm_snapshot.resize((cw, ch), Image.LANCZOS)
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

        def _draw_arrow(fx: int, fy: int, tx: int, ty: int,
                        color: tuple, wd: int = 2) -> None:
            """Draw an arrow from (fx,fy) to (tx,ty).

            Shaft is thin (same as rope markers), arrowhead is a small
            filled triangle, slightly chunkier than the shaft.
            """
            import math
            dx1: int = int(fx * rx)
            dy1: int = int(fy * ry)
            dx2: int = int(tx * rx)
            dy2: int = int(ty * ry)

            # Shaft
            draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=wd)

            if dx1 == dx2 and dy1 == dy2:
                return

            angle = math.atan2(dy2 - dy1, dx2 - dx1)
            # Short shaft — stop a bit before the arrowhead base
            head_len: float = 5.0      # arrowhead length (preview px)
            head_half: float = 2.5     # arrowhead half-width  (preview px)
            a1 = angle + math.radians(150)
            a2 = angle - math.radians(150)
            hx1 = int(dx2 + head_len * math.cos(a1))
            hy1 = int(dy2 + head_len * math.sin(a1))
            hx2 = int(dx2 + head_len * math.cos(a2))
            hy2 = int(dy2 + head_len * math.sin(a2))
            # Filled triangle
            draw.polygon([(dx2, dy2), (hx1, hy1), (hx2, hy2)], fill=color)
            # Slightly thicker outline at the base of the arrowhead
            mid_back = ((hx1 + hx2) // 2, (hy1 + hy2) // 2)
            draw.line([(hx1, hy1), (hx2, hy2)],
                      fill=color, width=max(wd, 3))

        for i, r in enumerate(new_jumps):
            fx, fy, tx, ty = r[0], r[1], r[2], r[3]
            is_sel = (selected_source == "new" and selected_idx == i)
            color = RED if is_sel else CYAN
            _draw_arrow(fx, fy, tx, ty, color, wd=4 if is_sel else 2)

        for i, r in enumerate(old_jumps):
            frm = r["from"]
            to = r["to"]
            is_sel = (selected_source == "old" and selected_idx == i)
            color = RED if is_sel else BLUE
            _draw_arrow(frm["x"], frm["y"], to["x"], to["y"],
                        color, wd=4 if is_sel else 2)

        new_count = len(new_jumps)
        old_count = len(old_jumps)
        title = f"{map_name} - 新{new_count}个 旧{old_count}个"
        draw.text((int(6 * ratio), int(3 * ratio)), title,
                  fill=(60, 60, 60), font=font)
        return img

    def _jump_save(self, map_name: str, jumps: list) -> None:
        """Save jump points to maps.json (jumps field)."""
        if not map_name:
            return
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
        else:
            data = {}

        existing: dict = data.get(map_name, {})
        existing["jumps"] = jumps
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "platforms" not in existing:
            existing["platforms"] = []
        if "ropes" not in existing:
            existing["ropes"] = []
        data[map_name] = existing

        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.status_text.set(
            f" {len(jumps)}个跳跃点已保存至 {MAPS_FILE.name}")

    # ---- Flash / teleport marking ----

    def _on_flash_toggle(self) -> None:
        if self._mode is not None and self._mode != "flash":
            self.status_text.set(f"{self._mode}标记运行中，请先停止")
            return
        if self._mode == "flash":
            self._flash_stop()
        else:
            self._flash_start()

    def _flash_start(self) -> None:
        if not self._check_minimap_ready():
            return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get())
        mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get())
        mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb)
        self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"闪现点标记中... (地图: {map_name})")
        self.flash_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None
        self.frame_count = 0
        self.running = True
        self._mode = "flash"
        self._flash_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="flash")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(
            target=self._loop_flash, args=(map_name,), daemon=True)
        self.thread.start()

    def _flash_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self._flash_button_set_running(False)
        self._set_mode_buttons("normal")
        self.confirm_btn.config(state="normal")
        self._mode = None
        if self.flash_detector.count > 0:
            self._flash_review_and_save()
        else:
            self.status_text.set("闪现点标记已停止 | 未检测到闪现")

    def _flash_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["flash"]
        if running:
            btn.config(text="停止闪现点标记", bg="#ff6b6b", activebackground="#e85a5a")
        else:
            btn.config(text="闪现点标记", bg="#f39c12", activebackground="#e67e22")

    def _loop_flash(self, map_name: str) -> None:
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()
        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt:
                    time.sleep(0.1); continue
                ml, mt, mr, mb = self.mm_offsets
                if mr - ml <= 0 or mb - mt <= 0:
                    time.sleep(0.1); continue
                region: dict = {"left": gl + ml, "top": gt + mt,
                                "width": mr - ml, "height": mb - mt}
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None:
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None:
                    self.flash_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"闪现点标记中... {self.frame_count}帧 | "
                        f"已检测{self.flash_detector.count}次闪现")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}")
                break
            sleep_t = interval - (time.time() - t0)
            if sleep_t > 0:
                time.sleep(sleep_t)
        msg = f"已停止  {self.frame_count}帧 | 检测到{self.flash_detector.count}次闪现"
        self.root.after(0, self.status_text.set, msg)

    # ---- Flash Review & Save ----

    def _flash_review_and_save(self) -> None:
        try:
            self._flash_review_and_save_impl()
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
            return ImageTk.PhotoImage(self._draw_flash_preview(
                map_name, new_flash, old_flash, state["source"], state["idx"],
                target_size=(dw, dh)))

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅闪现点 - {map_name}")
        review_win.transient(self.root)
        review_win.grab_set()

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
        lf = tk.Frame(right)
        lf.pack(fill="both", expand=True)
        lb = tk.Listbox(lf, font=("Consolas", 9), width=32, height=14,
                        selectmode="single", exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lf, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.configure(yscrollcommand=sb.set)

        def _fill(): lb.delete(0, "end"); [lb.insert("end", L) for L in labels]
        _fill()

        def on_sel(_e=None):
            s = lb.curselection()
            if s:
                it = items[s[0]]
                state["source"], state["idx"] = it["source"], it["idx"]
            else:
                state["source"], state["idx"] = None, -1
            refresh_preview()
        lb.bind("<<ListboxSelect>>", on_sel)

        tk.Frame(right, height=1, bg="#ccc").pack(fill="x", pady=6)
        bf = tk.Frame(right)
        bf.pack(side="bottom", fill="x", pady=4)

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
            ew.title("编辑闪现点"); ew.transient(review_win); ew.grab_set()
            ew.resizable(False, False)
            if it["source"] == "new": fx, fy, tx, ty, *_ = it["data"]
            else: frm, to = it["data"]["from"], it["data"]["to"]; fx, fy, tx, ty = frm["x"], frm["y"], to["x"], to["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="起始点", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            fx_v = tk.StringVar(value=str(fx))
            fy_v = tk.StringVar(value=str(fy))
            tx_v = tk.StringVar(value=str(tx))
            ty_v = tk.StringVar(value=str(ty))
            for r, lbl, var in [(1, "X:", fx_v), (2, "Y:", fy_v)]:
                tk.Label(f, text=lbl, font=("Microsoft YaHei", 9)).grid(row=r, column=0, sticky="e", padx=(0, 4))
                tk.Entry(f, textvariable=var, width=6, font=("Consolas", 10)).grid(row=r, column=1)
            tk.Label(f, text="终点", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            for r, lbl, var in [(4, "X:", tx_v), (5, "Y:", ty_v)]:
                tk.Label(f, text=lbl, font=("Microsoft YaHei", 9)).grid(row=r, column=0, sticky="e", padx=(0, 4))
                tk.Entry(f, textvariable=var, width=6, font=("Consolas", 10)).grid(row=r, column=1)

            # type 选择
            tp_var = tk.StringVar(value="one_way")
            tp_row = tk.Frame(f)
            tp_row.grid(row=6, column=0, columnspan=2, pady=(8, 0))
            if it["source"] == "old":
                old_tp = it["data"].get("type", "one_way")
                tp_var.set(old_tp)
            tk.Label(tp_row, text="类型:", font=("Microsoft YaHei", 9)).pack(side="left")
            tk.Radiobutton(tp_row, text="单向", variable=tp_var, value="one_way",
                           font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
            tk.Radiobutton(tp_row, text="双向", variable=tp_var, value="two_way",
                           font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

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
                        "type": tp_var.get(),
                    }
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

    def _draw_flash_preview(self, map_name, new_flash, old_flash, sel_src, sel_idx, target_size=None):
        w, h = self.mm_size; w = w or 154; h = h or 156
        cw, ch = target_size or (w, h)
        rx, ry = cw / w, ch / h; ratio = (rx + ry) / 2
        if self._mm_snapshot and self._mm_snapshot.size == (w, h):
            img = self._mm_snapshot.resize((cw, ch), Image.LANCZOS).copy()
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
        import math
        for i, r in enumerate(new_flash):
            c = FL_COLOR
            if sel_src == "new" and sel_idx == i: c = (255, 0, 0)
            dx1, dy1 = int(r[0] * rx), int(r[1] * ry)
            dx2, dy2 = int(r[2] * rx), int(r[3] * ry)
            draw.line([(dx1, dy1), (dx2, dy2)], fill=c, width=2)
            if dx1 != dx2 or dy1 != dy2:
                ang = math.atan2(dy2 - dy1, dx2 - dx1)
                hl = 5.0; a1 = ang + math.radians(150); a2 = ang - math.radians(150)
                draw.polygon([(dx2, dy2),
                    (int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))),
                    (int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2)))], fill=c)
                draw.line([(int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))),
                           (int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2)))], fill=c, width=3)
        for i, r in enumerate(old_flash):
            tp = r.get("type", "one_way")
            c = FL_2W if tp == "two_way" else FL_1W
            if sel_src == "old" and sel_idx == i: c = (255, 0, 0)
            frm, to = r["from"], r["to"]
            dx1, dy1 = int(frm["x"] * rx), int(frm["y"] * ry)
            dx2, dy2 = int(to["x"] * rx), int(to["y"] * ry)
            draw.line([(dx1, dy1), (dx2, dy2)], fill=c, width=2)
            if dx1 != dx2 or dy1 != dy2:
                ang = math.atan2(dy2 - dy1, dx2 - dx1)
                hl = 5.0; a1 = ang + math.radians(150); a2 = ang - math.radians(150)
                draw.polygon([(dx2, dy2),
                    (int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))),
                    (int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2)))], fill=c)
                draw.line([(int(dx2 + hl * math.cos(a1)), int(dy2 + hl * math.sin(a1))),
                           (int(dx2 + hl * math.cos(a2)), int(dy2 + hl * math.sin(a2)))], fill=c, width=3)
        draw.text((int(6 * ratio), int(3 * ratio)),
                  f"{map_name} - 新{len(new_flash)}个 旧{len(old_flash)}个",
                  fill=(60, 60, 60), font=font)
        return img

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

    # ---- 5. Rope ladder marking ----

    def _on_rope_toggle(self) -> None:
        if self._mode == "platform":
            self.status_text.set("平台标记运行中，请先停止")
            return
        if self._mode == "jump":
            self.status_text.set("跳跃点标记运行中，请先停止")
            return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止")
            return
        if self._mode == "rope":
            self._rope_stop()
        else:
            self._rope_start()

    def _rope_start(self) -> None:
        if not self._check_minimap_ready():
            return
        map_name: str = self.map_name_var.get().strip()

        ml: int = int(self.mm_left_var.get())
        mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get())
        mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb)
        self.mm_size = (mr - ml, mb - mt)

        self.status_text.set(f"绳梯标记中... 检测绳梯顶/底 (地图: {map_name})")

        self.rope_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None
        self.frame_count = 0
        self.running = True
        self._mode = "rope"
        self._rope_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="rope")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(
            target=self._loop_rope, args=(map_name,), daemon=True)
        self.thread.start()

    def _rope_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self._rope_button_set_running(False)
        self._set_mode_buttons("normal")
        self.confirm_btn.config(state="normal")
        self._mode = None

        if self.rope_detector.count > 0:
            self._rope_review_and_save()
        else:
            self.status_text.set("绳梯标记已停止 | 未检测到任何绳梯")

    def _rope_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["rope"]
        if running:
            btn.config(text="停止绳梯标记", bg="#ff6b6b", activebackground="#e85a5a")
        else:
            btn.config(text="绳梯标记", bg="#e67e22", activebackground="#d35400")

    def _loop_rope(self, map_name: str) -> None:
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()

        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt:
                    time.sleep(0.1)
                    continue

                ml, mt, mr, mb = self.mm_offsets
                ml_abs: int = gl + ml
                mt_abs: int = gt + mt
                mw: int = mr - ml
                mh: int = mb - mt

                if mw <= 0 or mh <= 0:
                    time.sleep(0.1)
                    continue

                region: dict = {"left": ml_abs, "top": mt_abs,
                                "width": mw, "height": mh}
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]

                # Cache first minimap frame for preview background
                if self._mm_snapshot is None:
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])

                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None:
                    self.rope_detector.add(pos[0], pos[1])

                self.frame_count += 1

                now: float = time.time()
                if now - last_status > 0.5:
                    pending_hint: str = ""
                    if self.rope_detector.has_pending:
                        pending_hint = " | 等待底部..."
                    self.root.after(0, self.status_text.set,
                        f"绳梯标记中... {self.frame_count}帧 | "
                        f"已检测{self.rope_detector.count}条绳梯{pending_hint}")
                    last_status = now

            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}")
                break

            sleep_t: float = interval - (time.time() - t0)
            if sleep_t > 0:
                time.sleep(sleep_t)

        msg: str = (f"已停止  {self.frame_count}帧 | "
                    f"检测到{self.rope_detector.count}条绳梯")
        self.root.after(0, self.status_text.set, msg)

    # ---- Rope Review & Save ----

    def _rope_review_and_save(self) -> None:
        try:
            self._rope_review_and_save_impl()
        except Exception as e:
            import traceback
            err: str = traceback.format_exc()
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_rope_error.log", "w", encoding="utf-8") as f:
                f.write(err)
            self.status_text.set(f"绳梯审阅窗口错误: {e}")

    def _rope_review_and_save_impl(self) -> None:
        map_name: str = self.map_name_var.get().strip()
        # Convert in-memory tuples to mutable list, applying y_offset so
        # the preview / listbox / save all work with the final coordinates.
        yoff: int = self.rope_detector.y_offset
        new_ropes: list = [[tx, ty + yoff, bx, by + yoff]
                           for tx, ty, bx, by in self.rope_detector.ropes]

        # Load existing ropes for this map
        old_ropes: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
            old_ropes = data.get(map_name, {}).get("ropes", [])

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale)
        dh: int = int(sh * scale)

        # --- shared selection state ---
        state: dict = {"source": None, "idx": -1}  # ("new"|"old", idx)

        # --- combined rope list for Listbox ---
        rope_items: list = []       # [{"source","idx","data"}, ...]
        rope_labels: list[str] = []  # display strings

        def _rebuild_items() -> None:
            nonlocal rope_items, rope_labels
            rope_items = []
            rope_labels = []
            for i, r in enumerate(new_ropes):
                tx, ty, bx, by = r
                rope_items.append({"source": "new", "idx": i, "data": r})
                rope_labels.append(f"新{i + 1}: ({tx},{ty}) → ({bx},{by})")
            for i, r in enumerate(old_ropes):
                t: dict = r["top"]
                b: dict = r["bottom"]
                rope_items.append({"source": "old", "idx": i, "data": r})
                rope_labels.append(
                    f"旧{i + 1}: ({t['x']},{t['y']}) → ({b['x']},{b['y']})")

        _rebuild_items()

        def draw_preview() -> ImageTk.PhotoImage:
            img: Image.Image = self._draw_rope_preview(
                map_name, new_ropes, old_ropes,
                state["source"], state["idx"],
                target_size=(dw, dh))
            return ImageTk.PhotoImage(img)

        # ======== Build window ========
        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅绳梯 - {map_name}")
        review_win.transient(self.root)
        review_win.grab_set()

        # -- Left: minimap preview --
        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        initial_img: ImageTk.PhotoImage = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=initial_img)
        canvas.photo = initial_img

        def refresh_preview() -> None:
            new_img: ImageTk.PhotoImage = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=new_img)
            canvas.photo = new_img

        # -- Right panel --
        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)

        tk.Label(right_frame, text=f"绳梯标记: {len(rope_items)} 条",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))

        # -- Rope Listbox --
        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)

        listbox = tk.Listbox(list_frame, font=("Consolas", 9), width=32,
                             height=14, selectmode="single", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical",
                                 command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scrollbar.set)

        def _fill_listbox() -> None:
            listbox.delete(0, "end")
            for label in rope_labels:
                listbox.insert("end", label)

        _fill_listbox()

        def on_listbox_select(_event=None) -> None:
            sel = listbox.curselection()
            if sel:
                idx: int = sel[0]
                if idx < len(rope_items):
                    item: dict = rope_items[idx]
                    state["source"] = item["source"]
                    state["idx"] = item["idx"]
            else:
                state["source"] = None
                state["idx"] = -1
            refresh_preview()

        listbox.bind("<<ListboxSelect>>", on_listbox_select)

        # -- Separator --
        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=6)

        # -- Buttons --
        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=4)

        def delete_selected() -> None:
            sel = listbox.curselection()
            if not sel:
                self.status_text.set("请先在列表中选择一条绳梯")
                return
            idx: int = sel[0]
            if idx >= len(rope_items):
                return
            item: dict = rope_items[idx]
            if item["source"] == "new":
                new_ropes.pop(item["idx"])
            else:
                old_ropes.pop(item["idx"])
            state["source"] = None
            state["idx"] = -1
            _rebuild_items()
            _fill_listbox()
            refresh_preview()

        def save_and_close() -> None:
            all_saved: list = []
            for r in new_ropes:
                tx, ty, bx, by = r
                all_saved.append({
                    "top": {"x": tx, "y": ty},
                    "bottom": {"x": bx, "y": by},
                })
            all_saved.extend(old_ropes)
            self._rope_save(map_name, all_saved)
            review_win.destroy()

        def edit_selected() -> None:
            sel = listbox.curselection()
            if not sel:
                self.status_text.set("请先在列表中选择一条绳梯")
                return
            idx: int = sel[0]
            if idx >= len(rope_items):
                return
            item: dict = rope_items[idx]

            # Build edit popup
            edit_win = tk.Toplevel(review_win)
            edit_win.title("编辑绳梯坐标")
            edit_win.transient(review_win)
            edit_win.grab_set()
            edit_win.resizable(False, False)

            # Read current values
            if item["source"] == "new":
                tx, ty, bx, by = item["data"]
            else:
                t = item["data"]["top"]
                b = item["data"]["bottom"]
                tx, ty = t["x"], t["y"]
                bx, by = b["x"], b["y"]

            f = tk.Frame(edit_win, padx=15, pady=12)
            f.pack()

            tk.Label(f, text="顶部坐标", font=("Microsoft YaHei", 10, "bold")
                     ).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(
                row=1, column=0, sticky="e", padx=(0, 4))
            top_x_var = tk.StringVar(value=str(tx))
            tk.Entry(f, textvariable=top_x_var, width=6,
                     font=("Consolas", 10)).grid(row=1, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(
                row=2, column=0, sticky="e", padx=(0, 4))
            top_y_var = tk.StringVar(value=str(ty))
            tk.Entry(f, textvariable=top_y_var, width=6,
                     font=("Consolas", 10)).grid(row=2, column=1)

            tk.Label(f, text="底部坐标", font=("Microsoft YaHei", 10, "bold")
                     ).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(
                row=4, column=0, sticky="e", padx=(0, 4))
            bot_x_var = tk.StringVar(value=str(bx))
            tk.Entry(f, textvariable=bot_x_var, width=6,
                     font=("Consolas", 10)).grid(row=4, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(
                row=5, column=0, sticky="e", padx=(0, 4))
            bot_y_var = tk.StringVar(value=str(by))
            tk.Entry(f, textvariable=bot_y_var, width=6,
                     font=("Consolas", 10)).grid(row=5, column=1)

            def _apply_edit() -> None:
                try:
                    ntx, nty = int(top_x_var.get()), int(top_y_var.get())
                    nbx, nby = int(bot_x_var.get()), int(bot_y_var.get())
                except ValueError:
                    self.status_text.set("坐标必须为整数")
                    return
                if item["source"] == "new":
                    new_ropes[item["idx"]] = [ntx, nty, nbx, nby]
                else:
                    old_ropes[item["idx"]] = {
                        "top": {"x": ntx, "y": nty},
                        "bottom": {"x": nbx, "y": nby},
                    }
                _rebuild_items()
                _fill_listbox()
                refresh_preview()
                edit_win.destroy()

            btn_f = tk.Frame(f)
            btn_f.grid(row=6, column=0, columnspan=2, pady=(12, 0))
            tk.Button(btn_f, text="确定", font=("Microsoft YaHei", 9, "bold"),
                      width=6, bg="#4ecdc4", fg="white",
                      command=_apply_edit).pack(side="left", padx=4)
            tk.Button(btn_f, text="取消", font=("Microsoft YaHei", 9),
                      width=6, command=edit_win.destroy).pack(side="left", padx=4)

        tk.Button(btn_frame, text="删除", font=("Microsoft YaHei", 10),
                  width=8, bg="#e74c3c", fg="white", cursor="hand2",
                  command=delete_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="编辑坐标", font=("Microsoft YaHei", 10),
                  width=8, bg="#3498db", fg="white", cursor="hand2",
                  command=edit_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"),
                  width=8, bg="#4ecdc4", fg="white", cursor="hand2",
                  command=save_and_close).pack(side="left", padx=3)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10),
                  width=8, cursor="hand2",
                  command=review_win.destroy).pack(side="left", padx=3)

        self.root.wait_window(review_win)

    def _draw_rope_preview(self, map_name: str,
                           new_ropes: list, old_ropes: list,
                           selected_source, selected_idx: int,
                           target_size=None) -> Image.Image:
        """Draw minimap with rope ladder lines.

        Colors:
          - New ropes  (just recorded)   → green
          - Old ropes  (from maps.json)  → yellow
          - Selected rope                → red   (thicker)
        """
        w, h = self.mm_size
        if w <= 0 or h <= 0:
            w, h = 154, 156

        if target_size is not None:
            canvas_w, canvas_h = target_size
        else:
            canvas_w, canvas_h = w, h
        ratio_x: float = canvas_w / w
        ratio_y: float = canvas_h / h
        ratio: float = (ratio_x + ratio_y) / 2

        # Background: minimap snapshot with semi-transparent white overlay
        if self._mm_snapshot is not None and self._mm_snapshot.size == (w, h):
            bg = self._mm_snapshot.resize((canvas_w, canvas_h), Image.LANCZOS)
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
            """Draw a rope ladder segment from top to bottom."""
            dx1: int = int(tx * ratio_x)
            dy1: int = int(ty * ratio_y)
            dx2: int = int(bx * ratio_x)
            dy2: int = int(by * ratio_y)
            draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=wd)
            # small filled circles at endpoints
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
            _rope_line(t["x"], t["y"], b["x"], b["y"],
                       color, wd=4 if is_sel else 2)

        new_count: int = len(new_ropes)
        old_count: int = len(old_ropes)
        title: str = f"{map_name} - 新{new_count}条 旧{old_count}条"
        draw.text((int(6 * ratio), int(3 * ratio)), title,
                  fill=(60, 60, 60), font=font)
        return img

    def _rope_save(self, map_name: str, ropes: list) -> None:
        """Save ropes list to maps.json."""
        if not map_name:
            return
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
        else:
            data = {}

        existing: dict = data.get(map_name, {})
        existing["ropes"] = ropes
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "platforms" not in existing:
            existing["platforms"] = []
        if "jumps" not in existing:
            existing["jumps"] = []
        if "flash_points" not in existing:
            existing["flash_points"] = []
        data[map_name] = existing

        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.status_text.set(
            f" {len(ropes)}条绳梯已保存至 {MAPS_FILE.name}")

    # ---- 6. Model generate ----

    def _on_model_generate(self) -> None:
        """自动关联平台/绳梯/跳跃/闪现 → 弹窗管理关联关系"""
        if self.running:
            self.status_text.set("标记运行中，请先停止")
            return
        map_name: str = self.map_name_var.get().strip()
        if not map_name:
            self.status_text.set("请先输入地图名称")
            return

        if not MAPS_FILE.exists():
            self.status_text.set("maps.json 不存在")
            return
        with open(MAPS_FILE, "r", encoding="utf-8") as f:
            data: dict = json.load(f)
        map_cfg: dict = data.get(map_name, {})
        platforms_raw: list = map_cfg.get("platforms", [])
        ropes: list = map_cfg.get("ropes", [])
        jumps: list = map_cfg.get("jumps", [])
        flashes: list = map_cfg.get("flash_points", [])

        if not platforms_raw:
            self.status_text.set("请先标记平台")
            return

        # 1. 分配平台 ID
        platforms = []
        for i, p in enumerate(platforms_raw):
            np = dict(p); np["_idx"] = i; platforms.append(np)
        platforms.sort(key=lambda p: p["avg_y"], reverse=True)
        for i, p in enumerate(platforms):
            p["id"] = f"platform_{i}"

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

        # 2. 生成边
        edges: list[dict] = []
        eid = 0
        def _add(typ, src, dst, **kw):
            nonlocal eid; eid += 1
            edges.append({"id": f"e{eid}", "type": typ, "from_platform": src, "to_platform": dst, **kw})

        for r in ropes:
            tx, ty = r["top"]["x"], r["top"]["y"]; bx, by = r["bottom"]["x"], r["bottom"]["y"]
            pt, pb = _find_platform(tx, ty), _find_platform(bx, by)
            if pt and pb and pt != pb:
                _add("rope", pb, pt, direction="上", from_pt={"x": bx, "y": by}, to_pt={"x": tx, "y": ty})
                _add("rope", pt, pb, direction="下", from_pt={"x": tx, "y": ty}, to_pt={"x": bx, "y": by})

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

        # --- UI ---

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)

        mgr = tk.Toplevel(self.root)
        mgr.title(f"地图模型 - {map_name}")
        mgr.transient(self.root); mgr.grab_set()

        # === 左侧：小地图 ===
        left = tk.Frame(mgr)
        left.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(left, width=dw, height=dh, highlightthickness=0)
        canvas.pack()

        sel_idx = [-1]  # 用列表包装以便在闭包中修改

        PLAT_COLOR = (80, 200, 255, 120); PLAT_HI = (255, 220, 80, 200)
        EDGE_COLORS = {"rope": (255, 200, 40), "jump": (180, 80, 220), "flash": (255, 100, 30)}
        HI_COLOR = (255, 50, 50)

        def _draw_map() -> ImageTk.PhotoImage:
            w2, h2 = sw, sh; rx, ry = dw / w2, dh / h2; rt = (rx + ry) / 2
            if self._mm_snapshot and self._mm_snapshot.size == (w2, h2):
                img = self._mm_snapshot.resize((dw, dh), Image.LANCZOS).copy()
            else:
                img = Image.new("RGB", (dw, dh), (245, 245, 240))
            draw = ImageDraw.Draw(img)
            try: fnt = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", max(6, int(8 * rt)))
            except Exception: fnt = ImageFont.load_default()

            si = sel_idx[0]
            sel_e = edir[si] if 0 <= si < len(edir) else None
            sel_from, sel_to = (sel_e["from_platform"], sel_e["to_platform"]) if sel_e else (None, None)

            # 平台
            for p in platforms:
                pts = p.get("all_points", [])
                if not pts: continue
                poly = [(int(x * rx), int(y * ry)) for x, y in pts]
                hi = (p["id"] in (sel_from, sel_to))
                draw.polygon(poly, fill=PLAT_HI if hi else PLAT_COLOR,
                             outline=(255, 255, 255, 230))
                cx = sum(x for x, _ in poly) // len(poly); cy = sum(y for _, y in poly) // len(poly)
                lbl = p["id"].replace("platform_", "P")
                tc = (255, 200, 50) if hi else (255, 255, 255)
                draw.text((cx - 10, cy - 8), lbl, fill=tc, font=fnt)

            # 边
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
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        # === 右侧：关联列表（中文）===
        right = tk.Frame(mgr)
        right.pack(side="right", fill="y", padx=10, pady=10)

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
                if e["type"] == "rope":
                    label += f" [{e.get('direction','?')}]"
                elif e["type"] == "flash":
                    ft = e.get("flash_type", "one_way")
                    label += " [单向]" if ft == "one_way" else " [双向]"
                lb.insert("end", label)

        _fill()

        def _on_select(evt) -> None:
            s = lb.curselection()
            sel_idx[0] = s[0] if s else -1
            _refresh()

        lb.bind("<<ListboxSelect>>", _on_select)

        # --- 操作按钮 ---
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
            tk.Button(bf2, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6,
                      bg="#4ecdc4", fg="white", command=_ap).pack(side="left", padx=4)
            tk.Button(bf2, text="取消", font=("Microsoft YaHei", 9), width=6,
                      command=ew.destroy).pack(side="left", padx=4)

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
            tk.Button(bf2, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6,
                      bg="#4ecdc4", fg="white", command=_ap).pack(side="left", padx=4)
            tk.Button(bf2, text="取消", font=("Microsoft YaHei", 9), width=6,
                      command=ew.destroy).pack(side="left", padx=4)

        def _save() -> None:
            output = {"map_name": map_name, "minimap_size": list(self.mm_size),
                      "mm_region": list(self.mm_offsets), "platforms": platforms, "edges": edir}
            out_path = os.path.join(OUTPUT_DIR, f"{map_name}_model.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            self.status_text.set(f"模型已保存: {out_path}")
            mgr.destroy()

        tk.Button(bbar, text="编辑", font=("Microsoft YaHei", 9),
                  width=6, bg="#3498db", fg="white", command=_edit).pack(side="left", padx=2)
        tk.Button(bbar, text="删除", font=("Microsoft YaHei", 9),
                  width=6, bg="#e74c3c", fg="white", command=_del).pack(side="left", padx=2)
        tk.Button(bbar, text="新增", font=("Microsoft YaHei", 9),
                  width=6, bg="#2ecc71", fg="white", command=_add).pack(side="left", padx=2)
        tk.Button(bbar, text="保存模型", font=("Microsoft YaHei", 9, "bold"),
                  width=10, bg="#8e44ad", fg="white", command=_save).pack(side="left", padx=6)

        self.root.wait_window(mgr)

    # ---- 7. View all markers ----

    def _on_view_markers(self) -> None:
        """Open a read-only popup showing the minimap with all platforms and ropes."""
        if self.running:
            self.status_text.set("标记运行中，请先停止")
            return
        map_name: str = self.map_name_var.get().strip()
        if not map_name:
            self.status_text.set("请先输入地图名称")
            return

        # Load data from maps.json
        platforms: list = []
        ropes: list = []
        jumps: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
            map_cfg = data.get(map_name, {})
            platforms = map_cfg.get("platforms", [])
            ropes = map_cfg.get("ropes", [])
            jumps = map_cfg.get("jumps", [])
            # 兼容旧字段名
            if not jumps:
                jumps = map_cfg.get("teleports", [])
            flashes = map_cfg.get("flash_points", [])

        if not platforms and not ropes and not jumps and not flashes:
            self.status_text.set(f"地图 '{map_name}' 尚无标记数据")
            return

        # Read mm_region (used for dimensions only — auto-capture disabled to
        # avoid grabbing stale game UI when mm_region is outdated)
        mm_region = map_cfg.get("mm_region")

        if mm_region and len(mm_region) == 4:
            mw, mh = mm_region[2] - mm_region[0], mm_region[3] - mm_region[1]
        else:
            mw, mh = self.mm_size
            if mw <= 0 or mh <= 0:
                mw, mh = 154, 156

        scale: float = min(6.0, 700 / max(mw, mh, 1))
        dw: int = int(mw * scale)
        dh: int = int(mh * scale)

        img: Image.Image = self._draw_markers_overview(
            map_name, platforms, ropes, jumps, flashes,
            target_size=(dw, dh), mm_size=(mw, mh))
        photo = ImageTk.PhotoImage(img)

        view_win = tk.Toplevel(self.root)
        view_win.title(f"查看标记 - {map_name}")
        view_win.transient(self.root)
        view_win.grab_set()
        view_win.resizable(False, False)

        canvas = tk.Canvas(view_win, width=dw, height=dh, highlightthickness=0)
        canvas.pack(padx=5, pady=5)
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.photo = photo

        self.root.wait_window(view_win)

    def _draw_markers_overview(self, map_name: str,
                               platforms: list, ropes: list, jumps: list,
                               flashes: list,
                               target_size=None, mm_size=None) -> Image.Image:
        """Draw minimap background with all saved platforms, rope ladders and jumps.

        Platforms are drawn with dots + simplified polyline in a single color.
        Rope ladders are drawn as yellow line segments with endpoint dots.
        """
        if mm_size is not None:
            w, h = mm_size
        else:
            w, h = self.mm_size
        if w <= 0 or h <= 0:
            w, h = 154, 156

        if target_size is not None:
            cw, ch = target_size
        else:
            cw, ch = w, h
        rx: float = cw / w
        ry: float = ch / h
        ratio: float = (rx + ry) / 2

        # Background — use cached snapshot only if its size matches
        if (self._mm_snapshot is not None
                and self._mm_snapshot.size == (w, h)):
            bg = self._mm_snapshot.resize((cw, ch), Image.LANCZOS)
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

        PLAT_COLOR = (155, 89, 182)   # purple — single color for all platforms
        ROPE_COLOR = (241, 196, 15)   # yellow

        # --- Draw platforms ---
        for plat in platforms:
            all_pts = plat.get("all_points", [])
            if not all_pts:
                continue

            # Draw dots for all points
            for px, py in all_pts:
                dx: int = int(px * rx)
                dy: int = int(py * ry)
                draw.ellipse([dx - 2, dy - 2, dx + 2, dy + 2], fill=PLAT_COLOR)

            # Draw simplified polyline from turning points
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
        JUMP_COLOR: tuple = (52, 152, 219)    # blue  = jump (bidirectional)
        import math as _m
        for j in jumps:
            frm = j.get("from", {})
            to = j.get("to", {})
            fx, fy = frm.get("x", 0), frm.get("y", 0)
            tx, ty = to.get("x", 0), to.get("y", 0)
            color = JUMP_COLOR
            dx1 = int(fx * rx)
            dy1 = int(fy * ry)
            dx2 = int(tx * rx)
            dy2 = int(ty * ry)
            # Thin shaft
            draw.line([(dx1, dy1), (dx2, dy2)], fill=color, width=2)
            if dx1 != dx2 or dy1 != dy2:
                angle = _m.atan2(dy2 - dy1, dx2 - dx1)
                # Small compact arrowhead
                hl: float = 5.0
                a1 = angle + _m.radians(150)
                a2 = angle - _m.radians(150)
                hx1 = int(dx2 + hl * _m.cos(a1))
                hy1 = int(dy2 + hl * _m.sin(a1))
                hx2 = int(dx2 + hl * _m.cos(a2))
                hy2 = int(dy2 + hl * _m.sin(a2))
                draw.polygon([(dx2, dy2), (hx1, hy1), (hx2, hy2)], fill=color)
                # Thicker base line
                draw.line([(hx1, hy1), (hx2, hy2)], fill=color, width=3)

        # --- Draw flash / teleport arrows ---
        FLASH_1WAY: tuple = (231, 76, 60)     # red   = one_way
        FLASH_2WAY: tuple = (46, 204, 113)    # green = two_way
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
                ang = _m.atan2(dy2 - dy1, dx2 - dx1)
                hl = 5.0; a1 = ang + _m.radians(150); a2 = ang - _m.radians(150)
                hx1, hy1 = int(dx2 + hl * _m.cos(a1)), int(dy2 + hl * _m.sin(a1))
                hx2, hy2 = int(dx2 + hl * _m.cos(a2)), int(dy2 + hl * _m.sin(a2))
                draw.polygon([(dx2, dy2), (hx1, hy1), (hx2, hy2)], fill=color)
                draw.line([(hx1, hy1), (hx2, hy2)], fill=color, width=3)
                # two_way: also draw arrow at start
                if tp == "two_way":
                    ang2 = _m.atan2(dy1 - dy2, dx1 - dx2)
                    hx3, hy3 = int(dx1 + hl * _m.cos(ang2 + _m.radians(150))), int(dy1 + hl * _m.sin(ang2 + _m.radians(150)))
                    hx4, hy4 = int(dx1 + hl * _m.cos(ang2 - _m.radians(150))), int(dy1 + hl * _m.sin(ang2 - _m.radians(150)))
                    draw.polygon([(dx1, dy1), (hx3, hy3), (hx4, hy4)], fill=color)
                    draw.line([(hx3, hy3), (hx4, hy4)], fill=color, width=3)

        # Title
        pcount: int = len(platforms)
        rcount: int = len(ropes)
        jcount: int = len(jumps)
        fcount: int = len(flashes)
        title: str = f"{map_name} - {pcount}平台 {rcount}绳梯 {jcount}跳跃 {fcount}闪现"
        draw.text((int(6 * ratio), int(3 * ratio)), title,
                  fill=(60, 60, 60), font=font)
        return img

def main():
    root = tk.Tk()
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"+{(sw - 440) // 2}+{(sh - 520) // 2}")
    MapMarkerApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        err = traceback.format_exc()
        ctypes.windll.user32.MessageBoxW(0, err, "地图标记 - Error", 0x10)
        raise
