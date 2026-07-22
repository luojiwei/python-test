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
import random
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

import numpy as np

import config
from config import (
    PROJECT_DIR, WINDOW_TITLE,
    PERCEPTION_INTERVAL, TICK_INTERVAL, LOGIC_INTERVAL,
    ATTACK_DISTANCE, ATTACK_VERTICAL, PLATFORM_TOLERANCE,
    discover_maps,
)
from input_utils import (
    KeySender, find_window_by_title, capture_frame,
)
from perception import (
    GameState, Calibrator,
)
from world_model import WorldModel
from commands import Command, ClimbCommand, decide
from key_actions import KeyActionManager
from map_loader import MapLoader
from perception_pipeline import PerceptionPipeline
from skill_manager import SkillManager
from transition import TransitionController


# ============================================================
# 巡逻路线解析
# ============================================================



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
        self.state = GameState()
        self.actions = KeyActionManager(self.keys, self.state, self._log_key_action)

        # --- 模块化组件 ---
        self.loader = MapLoader(
            status_cb=self._update_status, log_cb=self._log_error)
        self.skills = SkillManager(log_cb=self._log_error)
        # actions / perception / transition 在 start() 中延时初始化

        # --- 持久化日志 ---
        self._log_file: str = str(PROJECT_DIR / "run.log")

        # --- 位置校准器 ---
        self.calib = Calibrator()
        self.frame_count: int = 0
        self.yolo_model = None
        self._patrol_direction: str = "up"
        self._transition_in_progress: bool = False
        self._transition_start_time: float = 0.0
        self._current_command: Command | None = None
        self._last_logic: float = 0.0
        self._last_perception: float = 0.0
        self._facing_stuck_since: float = 0.0      # 同朝向持续时间
        self._facing_last_count: int = 0            # 上次怪物数
        self.wm: WorldModel | None = None

        # --- 决策配置 ---
        self.min_monsters_var = tk.IntVar(value=3)
        self.patrol_mode_var = tk.StringVar(value="auto_hunt")
        self.patrol_route_idx_var = tk.StringVar(value="")
        self._patrol_waypoints: list[tuple[float, float]] = []
        self._patrol_route_names: list[str] = []
        self._patrol_all_routes: list[list[tuple[float, float]]] = []
        self._current_waypoint_idx: int = 0

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
        self.skills.build_panel(config_tab)

        # 尝试加载缓存配置
        cache_path = PROJECT_DIR / "skill_config.json"
        cached_skills = SkillManager.load_cached_skills()
        cached_map: str = ""
        cached_decision: dict = {}
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                if isinstance(cache_data, dict):
                    cached_map = cache_data.get("map", "")
                    cached_decision = {
                        "patrol_mode": cache_data.get("patrol_mode", "auto_hunt"),
                        "route_name": cache_data.get("route_name", ""),
                        "min_monsters": cache_data.get("min_monsters", 3),
                    }
            except Exception:
                pass

        if cached_skills:
            for item in cached_skills:
                self.skills.add_row(
                    name=item.get("name", ""),
                    key_display=item.get("key_display", "PageUp"),
                    duration=str(item.get("duration", "")),
                    enabled=item.get("enabled", True),
                )
        else:
            self.skills.add_row()  # 默认左列
            self.skills.add_row()  # 默认右列

        # --- 决策配置 ---
        self._build_decision_config(config_tab)

        # 恢复缓存地图
        if cached_map and hasattr(self, 'map_var'):
            try:
                self.map_var.set(cached_map)
            except Exception:
                pass

        # 恢复缓存的决策配置
        if cached_decision:
            try:
                self.patrol_mode_var.set(cached_decision.get("patrol_mode", "auto_hunt"))
                self.min_monsters_var.set(int(cached_decision.get("min_monsters", 3)))
                self._on_patrol_mode_change()  # 根据 patrol_mode 显示对应面板
                if cached_decision.get("route_name"):
                    self._route_dropdown_var.set(cached_decision["route_name"])
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

    def _log_key_action(self, msg: str) -> None:
        """KeyActionManager 的语义化动作日志。"""
        self._log_error(f"[动作] {msg}")

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

        selected_route = self._route_dropdown_var.get()
        try:
            route_idx = self._patrol_route_names.index(selected_route) if selected_route else 0
        except (ValueError, IndexError):
            route_idx = 0

        try:
            result = self.loader.load(map_name, self.target_hwnd, default_route_idx=route_idx)
        except FileNotFoundError as e:
            messagebox.showerror("资源缺失", str(e))
            return
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
            return

        # 写入实例变量
        self.wm = result.world_model
        self.yolo_model = result.yolo_model
        self.template = result.template
        self.search_region = result.search_region
        self._patrol_route_names = result.patrol_route_names
        self._patrol_all_routes = result.patrol_all_routes
        self._patrol_waypoints = result.patrol_waypoints

        # 延时初始化依赖资源的组件
        self.skills.actions = self.actions
        self.perception = PerceptionPipeline(
            self.calib, self.template, self.search_region,
            self.yolo_model, self.wm, self.actions,
            log_cb=self._log_error)
        self.transition = TransitionController(
            self.actions,
            log_cb=self._log_error,
            nearby_monster_check=self._nearby_monster_on_platform)

        # 读取技能配置
        self.skills.read_configs()
        self.skills.reset_timers()
        self._log_error(f"[技能] 共加载 {len(self.skills.configs)} 个配置:")
        for i, cfg in enumerate(self.skills.configs):
            self._log_error(f"  #{i} 名称={cfg['name']} 键位={cfg['key']} 持续={cfg['duration']}s")

        # 保存缓存
        self.skills.save_cache(
            map_name, self.patrol_mode_var.get(),
            self._route_dropdown_var.get(), self.min_monsters_var.get())

        # 启动时释放技能
        self.skills.initial_cast()

        # 初始化朝向
        self.actions.turn(random.choice(('l', 'r')))

        self.frame_count = 0
        self._patrol_direction = "up"
        self.transition.reset()
        self._current_command = None
        self._current_waypoint_idx = 0
        self._last_logic = time.time()
        self._last_perception = time.time()
        self.running = True
        self.btn.config(text="停止打怪", bg="#95a5a6", activebackground="#7f8c8d")
        self._update_status("运行中...")
        # 清空上次运行日志
        try:
            with open(self._log_file, "w", encoding="utf-8") as _:
                pass
        except Exception:
            pass
        self._log_error("=== 自动打怪 v2 启动 ===")

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # --- 停止 ---

    def stop(self) -> None:
        self.running = False
        self.actions.force_release_all()
        self.skills.reset_timers()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.btn.config(text="开始打怪", bg="#e74c3c", activebackground="#c0392b")
        self._update_status(f"已停止 — 共 {self.frame_count} 帧")
        self._log_error("=== 脚本已停止，按键已释放 ===")

    # --- 辅助方法 ---

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

    # --- 主循环 ---

    def _loop(self) -> None:
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
                    self.perception.perceive(frame, self.state, target_hwnd, self.frame_count)
                    self._last_perception = now

                # ---- 技能计时器 ----
                self.skills.process(now)

                # ---- 决策 ----
                logic_interval = 0.3 if self.patrol_mode_var.get() == "fixed_route" else LOGIC_INTERVAL
                if now - self._last_logic >= logic_interval and wm:
                    if self.transition.in_progress:
                        tr = self.transition.check(
                            self._current_command, self.state.player_minimap_y, now)
                        if tr.action in ("complete", "interrupt"):
                            self._current_command = None
                            self._log_error(tr.log_message)
                    else:
                        cmd, self._patrol_direction, self._current_waypoint_idx, log_text = decide(
                            self.state, wm, self._patrol_direction,
                            self.transition.in_progress,
                            self.min_monsters_var.get(),
                            patrol_mode=self.patrol_mode_var.get(),
                            patrol_waypoints=self._patrol_waypoints,
                            current_waypoint_idx=self._current_waypoint_idx)

                        if cmd is not None:
                            self._current_command = cmd
                            if cmd.is_transition():
                                self.transition.begin(now)
                        self._log_decision(log_text)
                    self._last_logic = now

                    # 朝向僵死检测
                    facing_now = self.state.facing
                    same_plat_same_dir = [m for m in self.state.monsters
                        if abs(m["y2"] - self.state.player_screen_y) <= PLATFORM_TOLERANCE
                        and ((m["cx"] - self.state.player_screen_x >= 0) == (facing_now == 'r'))]
                    mc_now = len(same_plat_same_dir)
                    if mc_now == 0 or self._facing_stuck_since == 0.0 or mc_now < self._facing_last_count:
                        self._facing_stuck_since = now
                        self._facing_last_count = mc_now
                    elif now - self._facing_stuck_since > 5.0 and facing_now in ('l', 'r'):
                        self._facing_stuck_since = now
                        self.actions.wake_up()
                        self._log_error(f"朝向僵死检测: 5s同朝向({facing_now})怪物未减({mc_now}只)，重按{facing_now}+攻击校准")

                # ---- 执行 (每 tick) ----
                if self._current_command and wm:
                    self._current_command.execute_tick(self.actions, self.state, wm)

            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                self._log_error(f"[严重异常] {e}\n{tb}")
                self.actions.force_release_all()
                time.sleep(0.5)

            elapsed = time.time() - t0
            sleep_t = TICK_INTERVAL - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)

        self.actions.force_release_all()

    # --- 技能面板 & 计时器 ---

    def _build_decision_config(self, parent: tk.Widget) -> None:
        """构建决策配置面板"""
        panel = tk.LabelFrame(parent, text="决策配置",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=8, pady=5, fg="#2c3e50")
        panel.pack(side="top", fill="x", padx=15, pady=(5, 2))

        # 巡逻方式（单选框 + 路线下拉框）
        mode_row = tk.Frame(panel)
        mode_row.pack(fill="x", pady=(0, 6))
        tk.Label(mode_row, text="巡逻方式:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 10))
        tk.Radiobutton(mode_row, text="自动寻怪", variable=self.patrol_mode_var,
                       value="auto_hunt", font=("Microsoft YaHei", 9),
                       command=self._on_patrol_mode_change).pack(side="left", padx=(0, 8))
        tk.Radiobutton(mode_row, text="固定路线", variable=self.patrol_mode_var,
                       value="fixed_route", font=("Microsoft YaHei", 9),
                       command=self._on_patrol_mode_change).pack(side="left")

        # 路线选择下拉框（固定路线时显示）
        self._route_dropdown_frame = tk.Frame(mode_row)
        self._route_dropdown_frame.pack(side="left", padx=(8, 0))
        self._route_dropdown_var = tk.StringVar(value="")
        self._route_dropdown = tk.OptionMenu(self._route_dropdown_frame,
                                              self._route_dropdown_var, "")
        self._route_dropdown.config(font=("Microsoft YaHei", 9), width=18, anchor="w")
        self._route_dropdown.pack(side="left")
        # 初始隐藏路线下拉，显示怪物配置
        self._route_dropdown_frame.pack_forget()

        # 自动寻怪配置（身边怪物最少数）
        self._monster_config_frame = tk.Frame(panel)
        self._monster_config_frame.pack(fill="x")

        tk.Label(self._monster_config_frame, text="身边怪物最少数:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))

        spin = tk.Spinbox(self._monster_config_frame, from_=1, to=20, increment=1, width=5,
                          textvariable=self.min_monsters_var,
                          font=("Microsoft YaHei", 10),
                          justify="center")
        spin.pack(side="left")

        tk.Label(self._monster_config_frame, text="  (当前平台怪物数少于此值则切换平台)",
                 font=("Microsoft YaHei", 8), fg="#888").pack(side="left")

    def _on_patrol_mode_change(self) -> None:
        """巡逻方式变更时的处理"""
        mode = self.patrol_mode_var.get()
        if mode == "fixed_route":
            self._monster_config_frame.pack_forget()
            # 如果路线数据还未加载，尝试当场加载
            if not self._patrol_route_names:
                map_name = self.map_var.get().strip()
                if map_name and map_name != "(无可用地图)":
                    map_dir = config.PROJECT_DIR / "maps" / map_name
                    markers_path = map_dir / "markers.json"
                    try:
                        names, coords = _load_patrol_routes(map_name, markers_path)
                        if names:
                            self._patrol_route_names = names
                            self._patrol_all_routes = coords
                            self._patrol_waypoints = coords[0] if coords else []
                    except Exception:
                        pass

            if self._patrol_route_names:
                menu = self._route_dropdown["menu"]
                menu.delete(0, "end")
                for name in self._patrol_route_names:
                    menu.add_command(label=name,
                                     command=tk._setit(self._route_dropdown_var, name))
                self._route_dropdown_var.set(self._patrol_route_names[0])
                self._route_dropdown_frame.pack(side="left", padx=(8, 0))
                self.status_var.set(f"固定路线已就绪 ({len(self._patrol_route_names)}条可选)")
            else:
                self._route_dropdown_frame.pack_forget()
                self.status_var.set("⚠ 无巡逻路线数据，请先在地图标记工具中编辑并同步")
        else:
            self._route_dropdown_frame.pack_forget()
            self._monster_config_frame.pack(fill="x")
            self.status_var.set("自动寻怪模式已就绪")

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
