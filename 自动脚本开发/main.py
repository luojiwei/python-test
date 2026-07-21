#!/usr/bin/env python3
"""
auto_farm_v2.py — 命令驱动、跨平台自动寻路打怪脚本

架构: 主线程(GUI) + daemon 子线程(游戏循环)
  - 感知: 截图+YOLO+模板+小地图
  - 决策: 判断位置→生成命令 (每 1s)
  - 执行: 持续运行当前命令 (每 tick)
"""

import ctypes
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

import numpy as np

import config
from config import (
    PROJECT_DIR, WINDOW_TITLE,
    YOLO_INTERVAL, PERCEPTION_INTERVAL, TICK_INTERVAL, LOGIC_INTERVAL,
    SEARCH_BOTTOM_SKIP_PCT, ATTACK_DISTANCE, ATTACK_VERTICAL, PLATFORM_TOLERANCE,
    SKILL_KEY_CHOICES, SKILL_KEY_LOOKUP, SKILL_SAFETY_MARGIN,
    discover_maps, validate_map_resources,
)
from input_utils import (
    KeySender, find_window_by_title, force_foreground,
    capture_frame, capture_minimap,
)
from perception import (
    find_character, detect_monsters, find_yellow_dot, detect_on_rope, GameState,
)
from world_model import WorldModel, load_world_model
from commands import Command, ClimbCommand, MoveToCommand, decide


# ============================================================
# GUI + 主循环
# ============================================================

class AutoFarmV2App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.running: bool = False
        self.thread: threading.Thread | None = None
        self.target_hwnd: int | None = None
        self.template: np.ndarray | None = None
        self.search_region: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.keys = KeySender()
        self.keys.set_log_callback(self._on_key)

        # --- 持久化日志 ---
        self._log_file: str = str(PROJECT_DIR / "run.log")
        self.frame_count: int = 0
        self.yolo_model = None
        self._patrol_direction: str = "up"
        self._transition_in_progress: bool = False
        self._transition_start_time: float = 0.0
        self._current_command: Command | None = None
        self._last_logic: float = 0.0
        self._last_perception: float = 0.0
        self._edge_recover_dir: str | None = None
        self._edge_recover_ticks: int = 0
        self._char_lost_frames: int = 0       # 连续丢失角色帧数
        self._stuck_last_x: float = 0.0
        self._stuck_last_y: float = 0.0
        self._stuck_monster_count: int = 0
        self._stuck_since: float = 0.0
        self._stuck_action_time: float = 0.0
        self.state = GameState()
        self.wm: WorldModel | None = None

        # --- 技能状态 ---
        self._skill_rows: list[dict] = []         # 每行的 widget 引用
        self._skill_configs: list[dict] = []      # 运行时配置快照
        self._skill_last_cast: dict[int, float] = {}  # row_index -> 上次释放时间
        self._skill_add_btn: tk.Button | None = None
        self._skill_cols: list[tk.Frame] = []     # 两列的容器

        # --- 决策配置 ---
        self.min_monsters_var = tk.IntVar(value=3)

        # --- GUI ---
        root.title("自动打怪 v2")
        root.geometry("800x740")
        root.resizable(False, False)

        # 状态变量（先创建，footer 会用到）
        self.status_var = tk.StringVar(value="就绪 — 点击按钮开始")

        # --- 底部：游戏控制 footer（先打包，确保始终可见）---
        self._build_footer(root)

        # --- Notebook 分页 ---
        notebook = ttk.Notebook(root)
        notebook.pack(side="top", fill="both", expand=True, padx=5, pady=(5, 0))

        config_tab = tk.Frame(notebook)
        log_tab = tk.Frame(notebook)
        notebook.add(config_tab, text="配置")
        notebook.add(log_tab, text="日志")

        # ========================
        # Tab 1: 配置
        # ========================

        # --- 顶部：技能面板 ---
        self._build_skill_panel(config_tab)

        # 尝试加载缓存配置
        cache_path = PROJECT_DIR / "skill_config.json"
        cached_skills: list[dict] = []
        cached_map: str = ""
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                if isinstance(cache_data, list):
                    cached_skills = cache_data
                else:
                    cached_skills = cache_data.get("skills", [])
                    cached_map = cache_data.get("map", "")
            except Exception:
                pass

        if cached_skills:
            for item in cached_skills:
                self._add_skill_row(
                    name=item.get("name", ""),
                    key_display=item.get("key_display", "PageUp"),
                    duration=str(item.get("duration", "")),
                )
        else:
            self._add_skill_row()  # 默认左列
            self._add_skill_row()  # 默认右列

        # --- 决策配置 ---
        self._build_decision_config(config_tab)

        # 恢复缓存地图
        if cached_map and hasattr(self, 'map_var'):
            try:
                self.map_var.set(cached_map)
            except Exception:
                pass

        # ========================
        # Tab 2: 日志
        # ========================

        log_tab.grid_rowconfigure(0, weight=0)  # heading
        log_tab.grid_rowconfigure(1, weight=2)  # 决策日志
        log_tab.grid_rowconfigure(2, weight=0)  # heading
        log_tab.grid_rowconfigure(3, weight=1)  # 运行信息
        log_tab.grid_columnconfigure(0, weight=1)

        tk.Label(log_tab, text="决策日志",
                 font=("Microsoft YaHei", 10, "bold"), fg="#333").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        self.log_text = ScrolledText(log_tab, font=("Consolas", 9),
                                     wrap="word", state="disabled", bg="#1e1e1e", fg="#d4d4d4")
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 5))

        tk.Label(log_tab, text="运行信息",
                 font=("Microsoft YaHei", 10, "bold"), fg="#333").grid(row=2, column=0, sticky="w", padx=10, pady=(5, 2))
        self.err_text = ScrolledText(log_tab, font=("Consolas", 9),
                                     wrap="word", state="disabled", bg="#2d1e1e", fg="#f48771")
        self.err_text.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 5))

        # --- 全局警告 ---
        tk.Label(root, text="⚠ 运行中请勿操作键盘，点停止会释放所有按键",
                 font=("Microsoft YaHei", 7), fg="#f39c12").pack(pady=(0, 3))

    # --- 线程安全的日志输出 ---

    _decision_seq: int = 0  # 决策序号

    def _log_to_file(self, text: str) -> None:
        """追加到持久化日志文件"""
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                from datetime import datetime
                ts = datetime.now().strftime("%H:%M:%S")
                f.write(f"[{ts}] {text}\n")
        except Exception:
            pass

    def _log_decision(self, text: str) -> None:
        self._log_to_file(f"[决策] {text}")
        def _write() -> None:
            self._decision_seq += 1
            self.log_text.config(state="normal")
            # 保留最近 200 行防止内存暴涨
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 200:
                self.log_text.delete("1.0", f"{lines - 200}.0")
            self.log_text.insert("end", f"\n── #{self._decision_seq} ──\n{text}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(0, _write)

    def _log_error(self, text: str) -> None:
        self._log_to_file(f"[运行] {text}")
        def _write() -> None:
            self.err_text.config(state="normal")
            self.err_text.insert("end", f"{text}\n")
            self.err_text.see("end")
            self.err_text.config(state="disabled")
        self.root.after(0, _write)

    def _update_status(self, text: str) -> None:
        def _set() -> None:
            self.status_var.set(text)
        self.root.after(0, _set)

    _last_key_logged: float = 0.0  # 限频

    def _on_key(self, key: str, action: str) -> None:
        """按键日志（每秒最多 1 条，减少刷屏）"""
        now = time.time()
        if action == "tap" and now - self._last_key_logged >= 1.0:
            self._last_key_logged = now
            self._log_error(f"[按键] {key}")

    def toggle(self) -> None:
        if not self.running:
            self.start()
        else:
            self.stop()

    # --- 启动 ---

    def start(self) -> None:
        win = find_window_by_title(WINDOW_TITLE)
        if win is None:
            self.status_var.set(f"未找到 '{WINDOW_TITLE}' 窗口")
            return
        self.target_hwnd, title, gl, gt, gr, gb = win
        self.lbl_window.config(text=f"游戏: {title[:35]}  ({gr-gl}x{gb-gt})", fg="#333")

        map_name = self.map_var.get()
        if map_name.startswith("("):
            self._update_status("请选择有效地图")
            return

        missing = validate_map_resources(map_name)
        if missing:
            msg = f"地图 [{map_name}] 缺少以下文件:\n\n" + "\n".join(f"  • {m}" for m in missing)
            msg += f"\n\n请确保 maps/{map_name}/ 目录下包含所需资源。"
            messagebox.showerror("资源缺失", msg)
            return

        map_dir = PROJECT_DIR / "maps" / map_name
        wm_path = str(map_dir / "world_model.json")
        yolo_path = str(map_dir / "best.pt")
        config_path = map_dir / "config.json"

        with open(config_path, "r", encoding="utf-8") as f:
            map_cfg = json.load(f)

        template_rect = tuple(map_cfg.get("template_rect", [85, 728, 150, 745]))
        mm_region = tuple(map_cfg.get("mm_region", [8, 97, 128, 208]))

        self._update_status(f"加载世界模型 [{map_name}]...")
        try:
            self.wm = load_world_model(wm_path)
            self.wm.mm_region = list(mm_region)
        except Exception as e:
            messagebox.showerror("加载失败", f"无法加载世界模型:\n{wm_path}\n\n{e}")
            return
        self._log_error(f"世界模型: {len(self.wm.platforms)} 平台, {len(self.wm.edges)} 边")

        self._update_status("加载 YOLO 模型...")
        from ultralytics import YOLO
        try:
            self.yolo_model = YOLO(yolo_path)
        except Exception as e:
            messagebox.showerror("加载失败", f"无法加载 YOLO 模型:\n{yolo_path}\n\n{e}")
            return
        if hasattr(self.yolo_model, 'names'):
            config.CLASS_NAMES.clear(); config.CLASS_NAMES.update(self.yolo_model.names)
        elif hasattr(self.yolo_model.model, 'names'):
            config.CLASS_NAMES.clear(); config.CLASS_NAMES.update(self.yolo_model.model.names)
        self._log_error(f"YOLO: {len(config.CLASS_NAMES)}类  黑名单={config.NON_MONSTER_NAMES}")

        self._update_status("截取角色名模板...")
        try:
            force_foreground(self.target_hwnd)
        except Exception:
            pass
        time.sleep(0.4)
        frame = capture_frame(self.target_hwnd)
        if frame is None:
            self._update_status("截图失败")
            return
        tx, ty, tr, tb = template_rect
        self.template = frame[ty:tb, tx:tr]
        frame_h, frame_w = frame.shape[:2]
        skip_px = int(frame_h * SEARCH_BOTTOM_SKIP_PCT)
        self.search_region = (0, 0, frame_w, frame_h - skip_px)
        self._log_error(f"模板: ({tx},{ty})->({tr},{tb})  skip={skip_px}px")

        try:
            force_foreground(self.target_hwnd)
        except Exception:
            pass
        time.sleep(0.3)

        # 读取技能配置并重置计时器
        self._read_skill_configs()
        self._skill_last_cast.clear()
        self._log_error(f"[技能] 共加载 {len(self._skill_configs)} 个配置:")
        for i, cfg in enumerate(self._skill_configs):
            self._log_error(f"  #{i} 名称={cfg['name']} 键位={cfg['key']} 持续={cfg['duration']}s")

        # 保存技能配置 + 地图到缓存
        cache_data = {
            "map": self.map_var.get(),
            "skills": [],
        }
        for row in self._skill_rows:
            cache_data["skills"].append({
                "name": row["name_var"].get(),
                "key_display": row["key_var"].get(),
                "duration": row["dur_var"].get(),
            })
        try:
            with open(PROJECT_DIR / "skill_config.json", "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 启动时释放所有有效技能一次（有键位即释放）
        start_now = time.time()
        for i, cfg in enumerate(self._skill_configs):
            key = cfg.get("key", "")
            if key:
                self.keys.tap(key, duration=0.05)
                self._skill_last_cast[i] = start_now
                self._log_error(f"[技能] {cfg['name']} 初始释放 (键位:{key})")
                time.sleep(1.0)

        self.keys.tap('r', duration=0.05)
        self.state.facing = 'r'
        time.sleep(0.15)

        self.frame_count = 0
        self._patrol_direction = "up"
        self._transition_in_progress = False
        self._current_command = None
        self._edge_recover_ticks = 0
        self._char_lost_frames = 0
        self._last_logic = time.time()
        self._last_perception = time.time()
        self.running = True
        self.btn.config(text="停止打怪", bg="#95a5a6", activebackground="#7f8c8d")
        self._update_status("运行中...")
        self._log_error("=== 自动打怪 v2 启动 ===")

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # --- 停止 ---

    def stop(self) -> None:
        self.running = False
        self.keys.force_release_all()
        self._skill_last_cast.clear()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.btn.config(text="开始打怪", bg="#e74c3c", activebackground="#c0392b")
        self._update_status(f"已停止 — 共 {self.frame_count} 帧")
        self._log_error("=== 脚本已停止，按键已释放 ===")

    # --- 辅助方法 ---

    def _at_platform_edge(self) -> str | None:
        """如果角色在平台边缘被遮挡，返回应移动的方向 'l'/'r'，否则 None"""
        wm = self.wm
        if wm is None:
            return None
        px = self.state.player_minimap_x
        pid = self.state.current_platform
        if pid is None:
            return None
        for p in wm.platforms:
            if p["id"] == pid:
                left = float(p["left_endpoint"]["x"])
                right = float(p["right_endpoint"]["x"])
                if abs(px - left) <= 5:
                    return 'r'
                if abs(px - right) <= 5:
                    return 'l'
                break
        return None

    def _nearby_monster_on_platform(self) -> bool:
        """当前平台是否有在攻击范围内的怪物"""
        cy = self.state.player_screen_y
        cx = self.state.player_screen_x
        for m in self.state.monsters:
            if abs(m["y2"] - cy) <= PLATFORM_TOLERANCE:
                dx = abs(m["cx"] - cx)
                dy = abs(m["y2"] - cy)
                if dx < ATTACK_DISTANCE and dy < ATTACK_VERTICAL:
                    return True
        return False

    def _check_stuck(self, now: float) -> MoveToCommand | None:
        """5s内角色没位移且怪物数不变 → 返回强制移动命令，否则 None"""
        cx = self.state.player_screen_x
        cy = self.state.player_screen_y
        mc = len(self.state.monsters)

        moved = (abs(cx - self._stuck_last_x) > 10 or abs(cy - self._stuck_last_y) > 10)
        monsters_changed = (mc != self._stuck_monster_count)

        if moved or monsters_changed or self._stuck_since == 0.0:
            self._stuck_last_x = cx
            self._stuck_last_y = cy
            self._stuck_monster_count = mc
            self._stuck_since = now if self._stuck_since == 0.0 else self._stuck_since
            if moved or monsters_changed:
                self._stuck_since = now
            return None

        if now - self._stuck_since < 5.0:
            return None

        # 卡死，且距离上次强制移动 > 2s
        if now - self._stuck_action_time < 2.0:
            return None

        # 找最近的怪物
        nearest = None
        nearest_dist = float("inf")
        for m in self.state.monsters:
            d = ((m["cx"] - cx) ** 2 + (m["cy"] - cy) ** 2) ** 0.5
            if d < nearest_dist:
                nearest_dist = d
                nearest = m

        if nearest is None:
            self._stuck_since = now
            return None

        self._stuck_action_time = now
        self._log_error(f"卡死检测: 5s未移动，强制走向最近怪物 ({nearest_dist:.0f}px)")
        return MoveToCommand(nearest["cx"])

    # --- 主循环 ---

    def _loop(self) -> None:
        last_yolo_time: float = 0.0
        monsters: list[dict] = []
        target_hwnd = self.target_hwnd
        wm = self.wm

        while self.running:
            t0 = time.time()
            self.frame_count += 1

            try:
                if target_hwnd is None:
                    time.sleep(0.1)
                    continue

                frame = capture_frame(target_hwnd)
                if frame is None:
                    time.sleep(0.05)
                    continue

                now = time.time()

                # ---- 感知 ----
                if now - self._last_perception >= PERCEPTION_INTERVAL:
                    # 1) 角色屏幕定位（模板匹配，宠物名遮挡时可能失败）
                    char = find_character(frame, self.template, self.search_region)
                    if char is None:
                        self._char_lost_frames += 1
                        if self._edge_recover_ticks > 0:
                            self._edge_recover_ticks -= 1
                            if self._edge_recover_ticks == 0:
                                self._edge_recover_dir = None
                                self.keys.release_all()
                        elif self._char_lost_frames >= 5:
                            # 有小地图定位 → 只是被宠物名遮挡，不触发边缘恢复/释放按键
                            mm_valid = (self.state.player_minimap_x != 0
                                        and now - self._last_perception < 0.5)
                            if mm_valid:
                                self._char_lost_frames = 0  # 重置计数，避免后续触发
                                # 无声跳过，不干扰游戏
                            else:
                                edge_dir = self._at_platform_edge()
                                if edge_dir:
                                    self._edge_recover_dir = edge_dir
                                    self._edge_recover_ticks = 15
                                    self.keys.hold_only((edge_dir,))
                                    self._log_error(f"[{self.frame_count:04d}] 边缘遮挡，向{edge_dir}移动恢复")
                                else:
                                    self.keys.force_release_all()
                                    self._log_error(f"[{self.frame_count:04d}] 角色丢失")
                                    time.sleep(0.2)
                    else:
                        cx, cy, _conf = char
                        self.state.player_screen_x = cx
                        self.state.player_screen_y = cy
                        self._char_lost_frames = 0

                    # 2) YOLO 怪物检测
                    if now - last_yolo_time >= YOLO_INTERVAL:
                        try:
                            monsters = detect_monsters(self.yolo_model, frame)
                            last_yolo_time = now
                        except Exception as e:
                            self._log_error(f"[{self.frame_count:04d}] YOLO异常: {e}")
                    self.state.monsters = monsters

                    # 3) 小地图定位 + 绳梯检测（独立于角色模板匹配，爬梯时关键）
                    if wm:
                        mm = capture_minimap(target_hwnd, tuple(wm.mm_region))
                        if mm is not None:
                            dot = find_yellow_dot(mm)
                            if dot is not None:
                                self.state.player_minimap_x = dot[0]
                                self.state.player_minimap_y = dot[1]
                                pid = wm.find_platform(dot[0], dot[1])
                                if pid:
                                    self.state.current_platform = pid

                                # 绳梯检测：连续5帧 x重合且 y在绳梯Y范围内
                                was_on_rope = self.state.on_rope
                                if detect_on_rope(wm, dot[0], dot[1]):
                                    self.state.rope_frames += 1
                                    if self.state.rope_frames >= 5 and not self.state.on_rope:
                                        self.state.on_rope = True
                                else:
                                    self.state.rope_frames = 0
                                    self.state.on_rope = False
                                if self.state.on_rope != was_on_rope:
                                    if self.state.on_rope:
                                        self._log_error(f"[{self.frame_count:04d}] 检测到角色在绳梯上 "
                                                        f"(x={dot[0]:.0f}, y={dot[1]:.0f})")
                                    else:
                                        self._log_error(f"[{self.frame_count:04d}] 角色离开绳梯")

                        # 诊断：每2秒输出绳梯检测状态（不受mm=None影响）
                        if self.frame_count % 60 == 0:
                            if mm is None:
                                self._log_error(f"[{self.frame_count:04d}] 绳梯诊断: 小地图截图失败")
                            elif dot is None:
                                self._log_error(f"[{self.frame_count:04d}] 绳梯诊断: 黄点未找到 "
                                                f"(minimap={wm.mm_region})")
                            elif not self.state.on_rope:
                                px, py = self.state.player_minimap_x, self.state.player_minimap_y
                                nearest_rope = ""
                                nearest_dist = 9999.0
                                for e in wm.edges:
                                    if e.get("type") != "rope":
                                        continue
                                    rx = float(e.get("top", {}).get("x", 9999))
                                    ty = float(e.get("top", {}).get("y", 9999))
                                    by = float(e.get("bottom", {}).get("y", 9999))
                                    y_min, y_max = sorted([ty, by])
                                    dx = abs(px - rx)
                                    if y_min <= py <= y_max and dx < nearest_dist:
                                        nearest_dist = dx
                                        nearest_rope = f"x={rx:.0f} y=[{y_min:.0f},{y_max:.0f}]"
                                self._log_error(f"[{self.frame_count:04d}] 绳梯诊断: 黄点有 "
                                                f"pos=({px:.0f},{py:.0f})  "
                                                f"最近绳梯={nearest_rope or '无匹配'} dist={nearest_dist if nearest_dist < 9999 else '-'}")

                    self._last_perception = now

                # ---- 技能计时器 ----
                self._process_skills(now)

                # ---- 决策 (每 1s) ----
                if now - self._last_logic >= LOGIC_INTERVAL and wm:
                    if self._transition_in_progress:
                        finished = self._current_command and self._current_command.is_finished()
                        off_rope = (isinstance(self._current_command, ClimbCommand)
                                    and not self._current_command.is_on_rope(self.state.player_minimap_y))
                        if finished or off_rope:
                            self._current_command = None
                            self._transition_in_progress = False
                            self._stuck_since = now
                            self.state.facing = 'r'
                            self.keys.tap('r', duration=0.05)
                            self._log_error("到达目标平台，重置朝向↗，重新决策")
                        elif (isinstance(self._current_command, ClimbCommand)
                              and not self._current_command.is_on_rope(self.state.player_minimap_y)
                              and self._nearby_monster_on_platform()):
                            self._current_command = None
                            self._transition_in_progress = False
                            self._log_error("发现近身怪物，中断上梯优先清怪")
                    else:
                        cmd, self._patrol_direction, log_text = decide(
                            self.state, wm, self._patrol_direction,
                            self._transition_in_progress,
                            self.min_monsters_var.get())

                        stuck_cmd = self._check_stuck(now)
                        if stuck_cmd:
                            cmd = stuck_cmd
                            log_text += "\n动作: 强制移动(卡死检测)"

                        if cmd is not None:
                            self._current_command = cmd
                            if cmd.is_transition():
                                self._transition_in_progress = True
                                self._transition_start_time = now
                        self._log_decision(log_text)
                    self._last_logic = now

                # ---- 执行 (每 tick) ----
                if self._current_command and wm:
                    self._current_command.execute_tick(self.keys, self.state, wm)

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                self._log_error(f"[严重异常] {e}\n{tb}")
                self.keys.force_release_all()
                time.sleep(0.5)

            elapsed = time.time() - t0
            sleep_t = TICK_INTERVAL - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

        self.keys.force_release_all()

    # --- 技能面板 & 计时器 ---

    def _build_skill_panel(self, parent: tk.Widget) -> None:
        """构建 2 列布局的自动技能面板"""
        # ttk 样式：缩小 Combobox 字体
        style = ttk.Style()
        style.configure("SkillCombo.TCombobox", font=("Microsoft YaHei", 8))

        panel = tk.LabelFrame(parent, text="自动技能",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=8, pady=5, fg="#2c3e50")
        panel.pack(side="top", fill="x", padx=15, pady=(8, 2))

        # 两列容器
        cols_frame = tk.Frame(panel)
        cols_frame.pack(fill="x")
        self._skill_cols = [tk.Frame(cols_frame), tk.Frame(cols_frame)]
        self._skill_cols[0].pack(side="left", fill="x", expand=True, anchor="n")
        self._skill_cols[1].pack(side="left", fill="x", expand=True, anchor="n")

        # 每列的小表头
        for col in self._skill_cols:
            hdr = tk.Frame(col)
            tk.Label(hdr, text="技能名称", width=10, anchor="w",
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left", padx=2)
            tk.Label(hdr, text="键位", width=7,
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left", padx=2)
            tk.Label(hdr, text="持续(秒)", width=8,
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left", padx=2)
            hdr.pack(anchor="w", pady=(0, 2))

        # 添加按钮
        btn_frame = tk.Frame(panel)
        self._skill_add_btn = tk.Button(btn_frame, text="+ 添加技能",
                                        font=("Microsoft YaHei", 8),
                                        command=self._add_skill_row)
        self._skill_add_btn.pack(side="left")
        tk.Label(btn_frame, text="  最多10个",
                 font=("Microsoft YaHei", 7), fg="#999").pack(side="left")
        btn_frame.pack(anchor="w", pady=(4, 0))

    def _add_skill_row(self, name: str = "", key_display: str = "PageUp",
                       duration: str = "") -> None:
        """添加一行技能配置（左右交替：1→左，2→右，3→左，4→右 ...）"""
        if len(self._skill_rows) >= 10:
            return

        col_idx = len(self._skill_rows) % 2
        parent = self._skill_cols[col_idx]

        row_frame = tk.Frame(parent)
        row_frame.pack(fill="x", pady=1)

        # 名称
        name_var = tk.StringVar(value=name)
        tk.Entry(row_frame, textvariable=name_var, width=10,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

        # 键位下拉（ttk.Combobox，限制下拉高度≈300px）
        key_display_names = [d for d, _ in SKILL_KEY_CHOICES]
        key_var = tk.StringVar(value=key_display)
        key_combo = ttk.Combobox(row_frame, textvariable=key_var, values=key_display_names,
                                  height=10, width=7, state="readonly",
                                  style="SkillCombo.TCombobox")
        key_combo.pack(side="left", padx=2)

        # 持续时间
        dur_var = tk.StringVar(value=duration)
        tk.Entry(row_frame, textvariable=dur_var, width=5,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        tk.Label(row_frame, text="秒", font=("Microsoft YaHei", 8)).pack(side="left")

        # 删除按钮
        del_btn = tk.Button(row_frame, text="✕", font=("Microsoft YaHei", 9, "bold"),
                             fg="#e74c3c", width=2, relief="flat",
                             command=lambda f=row_frame: self._remove_skill_row(f))
        del_btn.pack(side="left", padx=(6, 0))

        self._skill_rows.append({
            "frame": row_frame,
            "name_var": name_var,
            "key_var": key_var,
            "dur_var": dur_var,
        })

        if len(self._skill_rows) >= 10 and self._skill_add_btn:
            self._skill_add_btn.config(state="disabled")

    def _remove_skill_row(self, row_frame: tk.Frame) -> None:
        """删除一行技能配置（直接删，整列表保持原顺序）"""
        for i, r in enumerate(self._skill_rows):
            if r["frame"] is row_frame:
                self._skill_rows.pop(i)
                row_frame.destroy()
                break
        if self._skill_add_btn:
            self._skill_add_btn.config(state="normal")

    def _build_decision_config(self, parent: tk.Widget) -> None:
        """构建决策配置面板"""
        panel = tk.LabelFrame(parent, text="决策配置",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=8, pady=5, fg="#2c3e50")
        panel.pack(side="top", fill="x", padx=15, pady=(5, 2))

        row = tk.Frame(panel)
        row.pack(fill="x")

        tk.Label(row, text="身边怪物最少数:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))

        spin = tk.Spinbox(row, from_=1, to=20, increment=1, width=5,
                          textvariable=self.min_monsters_var,
                          font=("Microsoft YaHei", 10),
                          justify="center")
        spin.pack(side="left")

        tk.Label(row, text="  (当前平台怪物数少于此值时切换平台)",
                 font=("Microsoft YaHei", 8), fg="#888").pack(side="left")

    def _read_skill_configs(self) -> None:
        """从 GUI 读取技能配置到 _skill_configs"""
        configs: list[dict] = []
        for row in self._skill_rows:
            name = row["name_var"].get().strip()
            key_display = row["key_var"].get()
            key_name = SKILL_KEY_LOOKUP.get(key_display, "")
            try:
                duration = float(row["dur_var"].get())
            except (ValueError, TypeError):
                duration = 0.0
            configs.append({"name": name or f"技能{len(configs)+1}",
                            "key": key_name,
                            "duration": duration})
        self._skill_configs = configs

    def _process_skills(self, now: float) -> None:
        """检查并释放到期的技能"""
        if not self.running:
            return
        for i, cfg in enumerate(self._skill_configs):
            key = cfg.get("key", "")
            duration = cfg.get("duration", 0.0)
            if not key or duration <= 0:
                continue

            interval = max(duration - SKILL_SAFETY_MARGIN, 1.0)
            last = self._skill_last_cast.get(i, 0.0)

            if now - last >= interval:
                self.keys.tap(key, duration=0.05)
                self._skill_last_cast[i] = now
                self._log_error(f"[技能] {cfg['name']} 释放 (键位:{key}, 间隔:{interval:.0f}s)")
                time.sleep(1.0)

    def _build_footer(self, parent: tk.Widget) -> None:
        """构建底部 footer：游戏窗口、地图选择、开始按钮、状态"""
        footer = tk.LabelFrame(parent, text="游戏控制",
                                font=("Microsoft YaHei", 10, "bold"),
                                padx=8, pady=5, fg="#2c3e50")
        footer.pack(side="bottom", fill="x", padx=15, pady=(5, 8))

        # 上一行：窗口 + 地图 + 按钮
        control_row = tk.Frame(footer)
        control_row.pack(fill="x", pady=(0, 4))

        self.lbl_window = tk.Label(control_row, text="游戏: (未选择)",
                                    font=("Microsoft YaHei", 9), fg="#888")
        self.lbl_window.pack(side="left", padx=(0, 15))

        map_frame = tk.Frame(control_row)
        map_frame.pack(side="left", padx=(0, 15))
        tk.Label(map_frame, text="地图:", font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))
        map_names = discover_maps()
        if not map_names:
            map_names = ["(无可用地图)"]
        self.map_var = tk.StringVar(value=map_names[0])
        self.map_combo = tk.OptionMenu(map_frame, self.map_var, *map_names)
        self.map_combo.config(font=("Microsoft YaHei", 9), width=14)
        self.map_combo.pack(side="left")

        self.btn = tk.Button(control_row, text="开始打怪",
                              font=("Microsoft YaHei", 12, "bold"),
                              width=10, height=1,
                              bg="#e74c3c", fg="white",
                              activebackground="#c0392b",
                              relief="flat", cursor="hand2",
                              command=self.toggle)
        self.btn.pack(side="right")

        # 下一行：状态
        tk.Label(footer, textvariable=self.status_var,
                 font=("Microsoft YaHei", 9), fg="#555").pack(anchor="w")

    def on_close(self) -> None:
        if self.running:
            self.stop()
        self.root.destroy()


# ============================================================
# 入口
# ============================================================

def main() -> None:
    root = tk.Tk()
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"+{(sw-800)//2}+{(sh-740)//2}")
    app = AutoFarmV2App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        try:
            from datetime import datetime
            log = PROJECT_DIR / "run.log"
            with open(str(log), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] [严重异常] {e}\n{err}\n")
        except Exception:
            pass
        ctypes.windll.user32.MessageBoxW(0, err, "auto_farm_v2 - 启动失败", 0x10)
        raise
