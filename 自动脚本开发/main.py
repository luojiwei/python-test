#!/usr/bin/env python3
"""
auto_farm_v2.py — 命令驱动、跨平台自动寻路打怪脚本

架构: 主线程(GUI) + daemon 子线程(游戏循环)
  - 感知: 截图+YOLO(怪物/玩家)+OCR(角色名)+小地图
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
    OCCUPATION_DATA, SKILL_RULE_CHOICES,
    SKILL_RULE_DISPLAY_TO_CODE, SKILL_RULE_CODE_TO_DISPLAY,
    SKILL_KEY_CHOICES, SKILL_KEY_LOOKUP,
    discover_maps,
)
from input_utils import (
    KeySender, find_window_by_title, capture_frame,
    force_foreground, enum_visible_windows,
)
from perception import (
    GameState, Calibrator,
)
from world_model import WorldModel
from commands import Command, ClimbCommand, TimedAttackCommand, TurnAndAttackCommand
from decision_strategies import create_strategies, nearest_monster
from key_actions import KeyActionManager
from map_loader import MapLoader
from perception_pipeline import PerceptionPipeline
from skill_manager import SkillManager
from transition import TransitionController, RopeStuckMonitor
from auto_potion import AutoPotion


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
        self.char_name: str = ""    # 角色名（玩家OCR模糊匹配定位用）
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
        self._strategy = None           # 决策策略，延迟到 start() 初始化
        self._patrol_direction: str = "up"
        self._transition_in_progress: bool = False
        self._transition_start_time: float = 0.0
        self._current_command: Command | None = None
        self._rope_monitor: RopeStuckMonitor = RopeStuckMonitor(log_cb=self._log_error)
        self._last_logic: float = 0.0
        self._last_perception: float = 0.0
        self._facing_stuck_since: float = 0.0      # 同朝向持续时间
        self._facing_last_count: int = 0            # 上次怪物数
        self._run_start_time: float = 0.0           # 运行开始时间
        self.wm: WorldModel | None = None

        # --- 职业配置 ---
        self.occupation_var = tk.StringVar(value="")
        self.single_skill_var = tk.StringVar(value="")
        self.aoe_skill_var = tk.StringVar(value="")
        self.skill_rule_var = tk.StringVar(value=SKILL_RULE_CODE_TO_DISPLAY["mixed"])
        self.single_skill_key_var = tk.StringVar(value="")
        self.aoe_skill_key_var = tk.StringVar(value="")
        self.normal_attack_key_var = tk.StringVar(value="Ctrl")
        self._single_skill_combo = None
        self._aoe_skill_combo = None
        self._single_key_combo = None
        self._aoe_key_combo = None

        # --- 调试截图开关 ---
        self._debug_enabled: bool = True
        self._window_map: dict[str, int] = {}  # 窗口标题 → hwnd

        # --- 决策配置 ---
        self.min_monsters_var = tk.IntVar(value=3)
        self.patrol_mode_var = tk.StringVar(value="auto_hunt")
        self.patrol_route_idx_var = tk.StringVar(value="")
        self._patrol_waypoints: list[tuple[float, float]] = []
        self._patrol_route_names: list[str] = []
        self._patrol_all_routes: list[list[tuple[float, float]]] = []
        self._patrol_return_method: str = "一直走"
        self._current_waypoint_idx: int = 0

        # --- 配置项统一注册表（自动缓存/恢复，新增配置项只需注册一行） ---
        self._config_registry: dict[str, tuple] = {}
        self._config_restored: bool = False   # 注册完成前禁止落盘，防止部分覆盖

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

        # 禁止左右方向键切换标签页（打怪时键盘焦点在 Notebook 上会误切 tab）
        notebook.bind("<Left>", lambda _e: "break")
        notebook.bind("<Right>", lambda _e: "break")
        notebook.bind("<Up>", lambda _e: "break")
        notebook.bind("<Down>", lambda _e: "break")

        # ========================
        # Tab 1: 配置
        # ========================

        # --- 地图配置 ---
        self._build_map_config(config_tab)

        # --- 职业配置 ---
        self._build_occupation_config(config_tab)

        # --- 顶部：技能面板 ---
        self.skills.build_panel(config_tab)

        # --- 加载缓存配置 ---
        self._config_cache_path: Path = PROJECT_DIR / "config_cache.json"
        cached_skills = SkillManager.load_cached_skills()
        # 职业/技能/决策/药水等配置项统一在 _restore_and_trace_config() 注册恢复

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

        # --- 自动药水配置 ---
        self._build_potion_config(config_tab)

        # 地图/决策/药水缓存恢复统一在 _restore_and_trace_config() 处理

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

        # --- 恢复缓存配置并注册自动保存 ---
        self._restore_and_trace_config()

    def _register_config_var(self, key: str, var, default,
                             restore_fn=None) -> None:
        """注册配置项：启动时自动恢复缓存，变更时自动保存。

        Args:
            key: 缓存键名
            var: tk 变量
            default: 无缓存时的默认值
            restore_fn: 可选自定义恢复函数（接收缓存值），
                        用于需要转换/校验的项（如 skill_rule code→display）
        """
        self._config_registry[key] = (var, default)
        cache = self._load_config()
        try:
            if restore_fn is not None:
                restore_fn(cache.get(key, default))
            else:
                var.set(cache.get(key, default))
        except Exception:
            pass
        var.trace_add("write", lambda *_: self._save_config())

    def _restore_and_trace_config(self) -> None:
        """统一注册所有配置项：恢复缓存 + 变更自动保存。

        新增配置项时，只需在这里加一行 _register_config_var(...)，
        无需再改 _save_config / _load_config。
        """
        cache = self._load_config()

        def _restore_skill_rule(val) -> None:
            code = val if val in SKILL_RULE_CODE_TO_DISPLAY else "mixed"
            self.skill_rule_var.set(SKILL_RULE_CODE_TO_DISPLAY[code])

        # --- 职业/技能 ---
        self._register_config_var("occupation", self.occupation_var, "")
        self._register_config_var("single_skill", self.single_skill_var, "")
        self._register_config_var("aoe_skill", self.aoe_skill_var, "")
        self._register_config_var("single_skill_key", self.single_skill_key_var, "")
        self._register_config_var("aoe_skill_key", self.aoe_skill_key_var, "")
        self._register_config_var("skill_rule", self.skill_rule_var,
                                  SKILL_RULE_CODE_TO_DISPLAY["mixed"],
                                  restore_fn=_restore_skill_rule)
        self._register_config_var("normal_attack_key",
                                  self.normal_attack_key_var, "Ctrl")

        # --- 地图 / 窗口 / 角色名 ---
        self._register_config_var("map", self.map_var, "")
        self._register_config_var("window_title", self.window_var, "冒险岛怀旧服")
        if hasattr(self, "char_name_var"):
            self._register_config_var("char_name", self.char_name_var, "")

        # --- 决策配置 ---
        self._register_config_var("patrol_mode", self.patrol_mode_var, "auto_hunt")
        self._register_config_var("min_monsters", self.min_monsters_var, 3)
        if hasattr(self, "_route_dropdown_var"):
            self._register_config_var("route_name", self._route_dropdown_var, "")

        # --- 自动药水 ---
        self._register_config_var("hp_enabled", self.hp_enabled_var, False)
        self._register_config_var("hp_mode", self.hp_mode_var, "百分比")
        self._register_config_var("hp_threshold", self.hp_threshold_var, "50")
        self._register_config_var("hp_key", self.hp_key_var, "Q")
        self._register_config_var("mp_enabled", self.mp_enabled_var, False)
        self._register_config_var("mp_mode", self.mp_mode_var, "百分比")
        self._register_config_var("mp_threshold", self.mp_threshold_var, "30")
        self._register_config_var("mp_key", self.mp_key_var, "W")

        # --- 调试截图 ---
        if hasattr(self, "_debug_var"):
            debug_val = cache.get("debug_screenshot", True)
            self._debug_enabled = debug_val
            self._debug_var.set(debug_val)
            self._debug_var.trace_add("write", lambda *_: self._save_config())
            self._config_registry["debug_screenshot"] = (self._debug_var, True)

        # ---- 依赖 UI 的恢复后刷新（var 恢复完成后再统一触发） ----
        self._config_restored = True   # 注册全部完成，允许落盘
        try:
            self._on_occupation_change()   # 职业无效会自动清空技能选择
        except Exception:
            pass
        try:
            self._on_patrol_mode_change()  # 刷新寻怪/固定路线面板
        except Exception:
            pass
        # 路线名：_on_patrol_mode_change 会重置为第一条，这里补回缓存值
        cached_route = cache.get("route_name", "")
        if cached_route and hasattr(self, "_route_dropdown_var"):
            try:
                self._route_dropdown_var.set(cached_route)
            except Exception:
                pass
        self._save_config()  # 落盘一次，补全新增配置键

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
        import datetime
        ts: str = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_to_file(f"[{ts}][决策] {text}")
        def _write() -> None:
            self.log_text.config(state="normal")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 200:
                self.log_text.delete("1.0", f"{lines - 200}.0")
            was_at_bottom: bool = self.log_text.yview()[1] >= 0.99
            self.log_text.insert("end", f"\n── {ts} ──\n{text}\n")
            if was_at_bottom:
                self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(0, _write)

    def _log_error(self, text: str) -> None:
        import datetime
        ts: str = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_to_file(f"[{ts}] {text}")
        def _write() -> None:
            self.err_text.config(state="normal")
            was_at_bottom: bool = self.err_text.yview()[1] >= 0.99
            self.err_text.insert("end", f"[{ts}] {text}\n")
            if was_at_bottom:
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
        # 从下拉选择的窗口获取 hwnd
        selected_title = self.window_var.get()
        self._on_window_dropdown_click()  # 确保 _window_map 已刷新
        target_hwnd = self._window_map.get(selected_title)
        if target_hwnd is None:
            win = find_window_by_title(selected_title)
            if win is None:
                self.status_var.set(f"未找到窗口: {selected_title}")
                return
            target_hwnd, title, gl, gt, gr, gb = win
        else:
            r = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(target_hwnd, ctypes.byref(r))
            gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
            title = selected_title
        self.target_hwnd = target_hwnd
        self._run_start_time = time.time()

        # 关键：启动前先把游戏窗口调到最前。
        # keybd_event 只注入到前台窗口——若游戏不是前台（点击开始按钮后焦点在 GUI），
        # 脚本的按键会发到 GUI 而非游戏，导致"决策步行但角色不动/不受控制"。
        try:
            force_foreground(target_hwnd)
        except Exception:
            pass

        map_name = self.map_var.get()
        if map_name.startswith("("):
            messagebox.showwarning("配置不完整", "请选择有效地图")
            return

        occupation = self.occupation_var.get()
        if not occupation or occupation not in OCCUPATION_DATA:
            messagebox.showwarning("配置不完整", "请先选择职业")
            return
        if not self.single_skill_var.get():
            messagebox.showwarning("配置不完整", "请选择单体技能")
            return
        if not self.single_skill_key_var.get():
            messagebox.showwarning("配置不完整", "请配置单体技能按键")
            return
        # 群体技能选了就必须配按键
        if self.aoe_skill_var.get() and not self.aoe_skill_key_var.get():
            messagebox.showwarning("配置不完整", "请配置群体技能按键")
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
        self.char_name = self.char_name_var.get().strip()
        self._patrol_route_names = result.patrol_route_names
        self._patrol_all_routes = result.patrol_all_routes
        self._patrol_waypoints = result.patrol_waypoints
        self._patrol_return_method = result.patrol_return_method

        # 延时初始化依赖资源的组件
        self.skills.actions = self.actions
        self.perception = PerceptionPipeline(
            self.calib,
            self.yolo_model, self.wm, self.actions,
            dot_hsv_lower=result.dot_hsv_lower,
            dot_hsv_upper=result.dot_hsv_upper,
            char_name=self.char_name,
            minimap_full=result.minimap_full,
            minimap_size=result.minimap_size,
            log_cb=self._log_error)
        self.transition = TransitionController(
            self.actions,
            log_cb=self._log_error,
            nearby_monster_check=self._nearby_monster_on_platform)

        # 自动药水
        self.potion = AutoPotion(
            hp_rect=list(result.hp_rect) if result.hp_rect else [],
            mp_rect=list(result.mp_rect) if result.mp_rect else [],
            key_sender=self.actions.keys,
        )

        # 读取技能配置
        self.skills.read_configs()
        self.skills.reset_timers()
        self._log_error(f"[技能] 共加载 {len(self.skills.configs)} 个配置:")
        for i, cfg in enumerate(self.skills.configs):
            self._log_error(f"  #{i} 名称={cfg['name']} 键位={cfg['key']} 持续={cfg['duration']}s")

        # 初始化决策策略（按 patrol_mode 分发到对应策略实例）
        self._strategy = create_strategies(self._get_effective_skill, log_cb=self._log_error)

        # 保存缓存（决策配置由 _save_config() 管理，此处只存 Buff）
        self.skills.save_cache()

        # 启动时释放技能
        self.skills.initial_cast()

        # 初始化朝向
        self.actions.turn(random.choice(('l', 'r')))

        self.frame_count = 0
        self._patrol_direction = "up"
        self.transition.reset()
        self._current_command = None
        # 自动检测当前位置，匹配最近途经点
        self._current_waypoint_idx = self._detect_start_waypoint()
        self._last_logic = time.time()
        self._last_perception = time.time()
        self.running = True
        self.btn.config(text="停止打怪", bg="#95a5a6", activebackground="#7f8c8d")
        self._update_status(f"运行中—已运行0秒")
        # 清空上次运行日志
        try:
            with open(self._log_file, "w", encoding="utf-8") as _:
                pass
        except Exception:
            pass
        self._log_error("=== 自动打怪 v2 启动 ===")

        # 同步药水配置
        self._apply_potion_config()
        if hasattr(self, 'potion'):
            self._log_error(
                f"[药水] HP={'启用' if self.potion.hp_enabled else '禁用'}"
                f" {self.potion.hp_mode} {self.potion.hp_threshold} 键={self.potion.hp_key} | "
                f"MP={'启用' if self.potion.mp_enabled else '禁用'}"
                f" {self.potion.mp_mode} {self.potion.mp_threshold} 键={self.potion.mp_key}"
            )

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
        elapsed = time.time() - self._run_start_time
        self._update_status(f"已停止—共运行{elapsed:.0f}秒")
        self._log_error("=== 脚本已停止，按键已释放 ===")
        self._save_config()

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

    def _on_debug_toggle(self) -> None:
        """调试截图开关回调。"""
        self._debug_enabled = self._debug_var.get()
        self._save_config()

    def _on_window_dropdown_click(self, event=None) -> None:
        """点击游戏窗口下拉时刷新窗口列表。"""
        windows = enum_visible_windows()
        titles = [title for _, title in windows]
        self.window_combo["values"] = titles
        self._window_map = {title: hwnd for hwnd, title in windows}

    # --- 配置持久化 ---

    def _load_config(self) -> dict:
        """加载缓存配置，返回 dict。失败返回空 dict。"""
        if not self._config_cache_path.exists():
            return {}
        try:
            with open(self._config_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_config(self) -> None:
        """保存所有注册的配置项到缓存文件（自动遍历注册表）。"""
        if not getattr(self, "_config_restored", False):
            return  # 配置恢复未完成，避免部分覆盖缓存文件
        try:
            data: dict = {}
            for key, (var, _default) in self._config_registry.items():
                try:
                    data[key] = var.get()
                except Exception:
                    pass
            # 特殊转换：技能释放规则存 code（UI 显示的是 display 名）
            rule_display = data.get("skill_rule", "")
            data["skill_rule"] = SKILL_RULE_DISPLAY_TO_CODE.get(
                rule_display, "mixed")
            # 调试截图开关同步（布尔字段与 tk 变量一致）
            data["debug_screenshot"] = self._debug_enabled
            with open(self._config_cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 保存失败不阻塞正常流程

    def _skip_passed_waypoints(self, wps: list[tuple[float, float]],
                                start_idx: int, px: float) -> int:
        """跳过已走过的途经点：路线方向上角色已越过则前进。

        公共逻辑，_detect_start_waypoint() 和 _recalc_waypoint() 共用。
        """
        idx = start_idx
        while idx < len(wps) - 1:
            wx1, _ = wps[idx]
            wx2, _ = wps[idx + 1]
            dx_route = wx2 - wx1
            if dx_route > 5 and px > wx1 + 5:
                idx += 1  # 路线向右，角色在途经点右边 → 已走过
            elif dx_route < -5 and px < wx1 - 5:
                idx += 1  # 路线向左，角色在途经点左边 → 已走过
            else:
                break
        return idx

    def _detect_start_waypoint(self) -> int:
        """启动时捕获一帧感知，找到角色最近的途经点索引，跳过已走过的。"""
        wps = self._patrol_waypoints
        if not wps or len(wps) < 2:
            return 0
        try:
            frame = capture_frame(self.target_hwnd)
            if frame is None:
                return 0
            self.perception.perceive(frame, self.state, self.target_hwnd, self.frame_count)
            if hasattr(self, 'potion'):
                self.potion.update(frame, log_cb=self._log_error)
            px: float = self.state.player_minimap_x
            py: float = self.state.player_minimap_y
            if px == 0 and py == 0:
                return 0
            best_idx: int = 0
            best_dist: float = float("inf")
            for i, (wx, wy) in enumerate(wps):
                dist = ((wx - px) ** 2 + (wy - py) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i

            idx = self._skip_passed_waypoints(wps, best_idx, px)

            if idx != best_idx:
                self._log_error(f"[启动] 检测位置 ({px:.0f},{py:.0f})，最近 #{best_idx}，已走过 → 从 #{idx} 开始")
            else:
                self._log_error(f"[启动] 检测位置 ({px:.0f},{py:.0f})，最近途经点 #{best_idx} (距离={best_dist:.0f}px)")
            return idx
        except Exception as e:
            self._log_error(f"[启动] 位置检测失败，从途经点0开始: {e}")
            return 0

    def _recalc_waypoint(self) -> int:
        """过渡完成后，从当前感知位置重算最近途经点（轻量，不截帧）。"""
        wps = self._patrol_waypoints
        if not wps or len(wps) < 2:
            return self._current_waypoint_idx
        try:
            px: float = self.state.player_minimap_x
            py: float = self.state.player_minimap_y
            if px == 0 and py == 0:
                return self._current_waypoint_idx

            best_idx: int = self._current_waypoint_idx
            best_dist: float = float("inf")
            for i, (wx, wy) in enumerate(wps):
                dist = ((wx - px) ** 2 + (wy - py) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i

            idx = self._skip_passed_waypoints(wps, best_idx, px)

            if idx != self._current_waypoint_idx:
                self._log_error(f"[重算] 位置 ({px:.0f},{py:.0f})，途经点 {self._current_waypoint_idx}→{idx} (最近#{best_idx})")
            return idx
        except Exception:
            return self._current_waypoint_idx

    _debug_frame_seq: int = 0

    def _save_debug_frame(self, frame: np.ndarray) -> None:
        """保存游戏窗口截图到 debug_frames 目录，标注角色/怪物信息，循环覆盖最近 50 帧。"""
        import cv2
        import config as cfg
        out_dir = Path(__file__).parent / "debug_frames"
        out_dir.mkdir(exist_ok=True)
        self._debug_frame_seq = (self._debug_frame_seq + 1) % 50
        fname = out_dir / f"frame_{self._debug_frame_seq:02d}.png"

        # 标注角色位置
        annotated = frame.copy()
        px, py = int(self.state.player_screen_x), int(self.state.player_screen_y)
        mmx, mmy = int(self.state.player_minimap_x), int(self.state.player_minimap_y)
        cv2.circle(annotated, (px, py), 8, (0, 255, 255), 2)  # 黄色圆
        cv2.putText(annotated, f"P({px},{py}) MM({mmx},{mmy})",
                    (px + 12, py - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

        # 标注怪物
        for m in self.state.monsters:
            mx, my = int(m["cx"]), int(m["y2"])
            cls_id = m.get("cls", -1)
            name = cfg.CLASS_NAMES.get(cls_id, f"c{cls_id}")
            cv2.circle(annotated, (mx, my), 6, (0, 0, 255), 2)  # 红色圆
            cv2.putText(annotated, f"{name}({mx},{my})",
                        (mx + 8, my - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

        cv2.imwrite(str(fname), annotated)
        # 兜底：OpenCV 5.x 的 imwrite 不支持中文路径，失败时用 imencode 绕过
        if not fname.exists():
            import cv2 as _cv2
            _, buf = _cv2.imencode(".png", annotated)
            fname.write_bytes(buf.tobytes())

    # --- 主循环 ---

    def _loop(self) -> None:
        target_hwnd = self.target_hwnd
        wm = self.wm
        self._log_error("[诊断] 主循环启动")

        while self.running:
            t0 = time.time()
            self.frame_count += 1

            # 每秒更新一次运行时长
            if self.frame_count % 30 == 0:
                elapsed = t0 - self._run_start_time
                self._update_status(f"运行中—已运行{elapsed:.0f}秒")

                # 前台守护：keybd_event 只注入到前台窗口。用户点击 GUI 看日志后
                # GUI 会抢走焦点，导致脚本按键发到 GUI（角色"不受控制/不动"）。
                # 周期把游戏窗口拉回前台，确保按键注入有效。
                try:
                    import ctypes as _ct
                    if _ct.windll.user32.GetForegroundWindow() != target_hwnd:
                        force_foreground(target_hwnd)
                except Exception:
                    pass

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
                    self.state.record_position(
                        self.state.player_minimap_x, self.state.player_minimap_y)
                    self._last_perception = now

                # ---- 技能计时器 ----
                if self.running:
                    self.skills.process(now)

                # ---- 自动药水（后台执行，不中断移动/攻击） ----
                if hasattr(self, 'potion'):
                    self.potion.update(frame, log_cb=self._log_error)

                # ---- 决策 ----
                logic_interval = 0.3 if self.patrol_mode_var.get() == "fixed_route" else LOGIC_INTERVAL
                if self.running and now - self._last_logic >= logic_interval and wm:
                    if self.transition.in_progress:
                        tr = self.transition.check(
                            self._current_command, self.state.player_minimap_y, now)
                        if tr.action in ("complete", "interrupt"):
                            self._current_command = None
                            self._log_error(tr.log_message)
                            # complete → 重算途经点继续前进
                            # interrupt → 保持当前途经点（等待下次决策重新规划）
                            if tr.action == "complete":
                                self._current_waypoint_idx = self._recalc_waypoint()
                    else:
                        patrol_mode = self.patrol_mode_var.get()
                        strategy = self._strategy.get(patrol_mode, self._strategy.get("auto_hunt"))
                        if strategy is None:
                            cmd, log_text = None, "无匹配策略"
                        else:
                            cmd, self._patrol_direction, self._current_waypoint_idx, log_text = strategy.decide(
                                self.state, wm, self._patrol_direction,
                                self.transition.in_progress,
                                self.min_monsters_var.get(),
                                patrol_waypoints=self._patrol_waypoints,
                                current_waypoint_idx=self._current_waypoint_idx,
                                return_method=self._patrol_return_method)

                        if cmd is not None:
                            self._current_command = cmd
                            if cmd.is_transition():
                                self.transition.begin(now)
                        self._log_decision(log_text)
                    self._last_logic = now

                    # 朝向僵死检测：YOLO 延迟导致背怪误判 → 暂时关闭
                    # (保留代码以备后续修复感知延迟后重新启用)
                    # facing_now = self.state.facing
                    # same_plat_same_dir = [m for m in self.state.monsters
                    #     if abs(m["y2"] - self.state.player_screen_y) <= PLATFORM_TOLERANCE
                    #     and ((m["cx"] - self.state.player_screen_x >= 0) == (facing_now == 'r'))]
                    # mc_now = len(same_plat_same_dir)
                    # if mc_now == 0 or self._facing_stuck_since == 0.0 or mc_now < self._facing_last_count:
                    #     self._facing_stuck_since = now
                    #     self._facing_last_count = mc_now
                    # elif now - self._facing_stuck_since > 5.0 and facing_now in ('l', 'r'):
                    #     self._facing_stuck_since = now
                    #     self.actions.wake_up()
                    #     self._log_error(f"朝向僵死检测: 5s同朝向({facing_now})怪物未减({mc_now}只)")

                # ---- 执行 (每 tick) ----
                if self.running and self._current_command and wm:
                    self._current_command.execute_tick(self.actions, self.state, wm)

                # ---- 绳梯卡住监控（独立于 ClimbCommand 生命周期） ----
                if self.running:
                    cmd = self._current_command
                    if isinstance(cmd, ClimbCommand) and cmd.is_in_climb_state() \
                            and not self._rope_monitor.is_active():
                        rope_x, direction = cmd.get_rope_info()
                        self._rope_monitor.activate(rope_x, direction)
                    self._rope_monitor.tick(self.actions, self.state)

                # ---- 调试截图 (每 tick) ----
                if self._debug_enabled and frame is not None:
                    self._save_debug_frame(frame)

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

    # --- 职业配置面板 ---

    def _on_occupation_change(self, *args) -> None:
        """职业名称变更时，重置所有技能及其按键配置。"""
        occ_name = self.occupation_var.get()
        if not occ_name or occ_name not in OCCUPATION_DATA:
            self.single_skill_var.set("")
            self.aoe_skill_var.set("")
            self.single_skill_key_var.set("")
            self.aoe_skill_key_var.set("")
            if self._single_skill_combo:
                self._single_skill_combo.config(state="disabled")
            if self._aoe_skill_combo:
                self._aoe_skill_combo.config(state="disabled")
            if self._single_key_combo:
                self._single_key_combo.config(state="disabled")
            if self._aoe_key_combo:
                self._aoe_key_combo.config(state="disabled")
            return

        data = OCCUPATION_DATA[occ_name]
        single_names = [s["name"] for s in data.get("single_skills", [])]
        aoe_names = [s["name"] for s in data.get("aoe_skills", [])]
        full_names = [s["name"] for s in data.get("fullscreen_skills", [])]

        if self._single_skill_combo:
            self._single_skill_combo.config(state="readonly")
            self._single_skill_combo["values"] = single_names
            self.single_skill_var.set(single_names[0] if single_names else "")

        if self._single_key_combo:
            self._single_key_combo.config(state="readonly")

        if self._aoe_skill_combo:
            self._aoe_skill_combo.config(state="readonly")
            self._aoe_skill_combo["values"] = aoe_names + full_names
            self.aoe_skill_var.set(aoe_names[0] if aoe_names else (full_names[0] if full_names else ""))

        if self._aoe_key_combo:
            self._aoe_key_combo.config(state="readonly")

    def _build_map_config(self, parent: tk.Widget) -> None:
        """构建地图配置面板（置于职业配置上方）。"""
        panel = tk.LabelFrame(parent, text="地图配置",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=8, pady=5, fg="#2c3e50")
        panel.pack(side="top", fill="x", padx=15, pady=(8, 2))

        map_frame = tk.Frame(panel)
        map_frame.pack(fill="x")

        tk.Label(map_frame, text="地图:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))
        map_names = discover_maps()
        if not map_names:
            map_names = ["(无可用地图)"]
        self.map_var = tk.StringVar(value=map_names[0])
        self.map_combo = ttk.Combobox(map_frame, textvariable=self.map_var,
                                       values=map_names, state="readonly",
                                       width=16, font=("Microsoft YaHei", 9))
        self.map_combo.pack(side="left")

    def _build_occupation_config(self, parent: tk.Widget) -> None:
        """构建职业配置面板"""
        panel = tk.LabelFrame(parent, text="职业配置",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=8, pady=5, fg="#2c3e50")
        panel.pack(side="top", fill="x", padx=15, pady=(8, 2))

        key_display_names = [d for d, _ in SKILL_KEY_CHOICES]

        # 第一行：职业名称 + 技能释放规则
        row1 = tk.Frame(panel)
        row1.pack(fill="x", pady=(0, 2))

        tk.Label(row1, text="职业名称:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        occ_names = list(OCCUPATION_DATA.keys())
        self._occ_combo = ttk.Combobox(row1, textvariable=self.occupation_var,
                                        values=occ_names, state="readonly",
                                        width=10, font=("Microsoft YaHei", 9))
        self._occ_combo.pack(side="left", padx=(0, 20))
        self._occ_combo.bind("<<ComboboxSelected>>", self._on_occupation_change)

        tk.Label(row1, text="技能释放规则:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        rule_display_names = [d for d, _ in SKILL_RULE_CHOICES]
        self._rule_combo = ttk.Combobox(row1, textvariable=self.skill_rule_var,
                                         values=rule_display_names, state="readonly",
                                         width=10, font=("Microsoft YaHei", 9))
        self._rule_combo.pack(side="left")

        # 第二行：单体技能 + 按键
        row2 = tk.Frame(panel)
        row2.pack(fill="x", pady=(0, 2))

        tk.Label(row2, text="单体技能:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        self._single_skill_combo = ttk.Combobox(row2, textvariable=self.single_skill_var,
                                                 values=[], state="disabled",
                                                 width=12, font=("Microsoft YaHei", 9))
        self._single_skill_combo.pack(side="left")
        tk.Label(row2, text=" 按键:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(4, 2))
        self._single_key_combo = ttk.Combobox(row2, textvariable=self.single_skill_key_var,
                                               values=key_display_names,
                                               state="disabled", width=8,
                                               font=("Microsoft YaHei", 9))
        self._single_key_combo.pack(side="left")

        # 第三行：群体技能 + 按键
        row3 = tk.Frame(panel)
        row3.pack(fill="x", pady=(0, 2))

        tk.Label(row3, text="群体技能:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        self._aoe_skill_combo = ttk.Combobox(row3, textvariable=self.aoe_skill_var,
                                              values=[], state="disabled",
                                              width=12, font=("Microsoft YaHei", 9))
        self._aoe_skill_combo.pack(side="left")
        tk.Label(row3, text=" 按键:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(4, 2))
        self._aoe_key_combo = ttk.Combobox(row3, textvariable=self.aoe_skill_key_var,
                                            values=key_display_names,
                                            state="disabled", width=8,
                                            font=("Microsoft YaHei", 9))
        self._aoe_key_combo.pack(side="left")

        # 第四行：普通攻击 + 按键
        row4 = tk.Frame(panel)
        row4.pack(fill="x")

        tk.Label(row4, text="普通攻击:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        self._normal_attack_combo = ttk.Combobox(row4,
                                                  textvariable=self.normal_attack_key_var,
                                                  values=key_display_names,
                                                  state="readonly", width=8,
                                                  font=("Microsoft YaHei", 9))
        self._normal_attack_combo.bind("<<ComboboxSelected>>",
                                        lambda e: self._save_config())
        self._normal_attack_combo.pack(side="left", padx=(4, 0))

    def _get_skill_info(self, skill_name: str) -> dict | None:
        """根据技能名在当前职业数据中查找完整信息。"""
        occ = self.occupation_var.get()
        if occ not in OCCUPATION_DATA:
            return None
        for cat in ("single_skills", "aoe_skills", "fullscreen_skills"):
            for s in OCCUPATION_DATA[occ].get(cat, []):
                if s["name"] == skill_name:
                    return {"name": s["name"], "range": s.get("range", -1),
                            "fullscreen": cat == "fullscreen_skills"}
        return None

    def _get_effective_skill(self, monster_count: int) -> dict:
        """根据释放规则和怪物数返回应使用的技能。

        返回 {"name": str|None, "key": str|None, "range": int, "fullscreen": bool}
        """
        rule_code = SKILL_RULE_DISPLAY_TO_CODE.get(self.skill_rule_var.get(), "mixed")
        single_name = self.single_skill_var.get()
        aoe_name = self.aoe_skill_var.get()
        single_key = SKILL_KEY_LOOKUP.get(self.single_skill_key_var.get(), "")
        aoe_key = SKILL_KEY_LOOKUP.get(self.aoe_skill_key_var.get(), "")

        single_info = self._get_skill_info(single_name) or {}
        aoe_info = self._get_skill_info(aoe_name) or {}
        aoe_valid = aoe_name and aoe_name != "无"

        def result(name, key, info):
            return {"name": name or None, "key": key or None,
                    "range": info.get("range", 0),
                    "fullscreen": info.get("fullscreen", False)}

        if rule_code == "single":
            return result(single_name, single_key, single_info)
        if rule_code == "aoe":
            if aoe_valid:
                return result(aoe_name, aoe_key, aoe_info)
            return result(single_name, single_key, single_info)
        # mixed
        if monster_count <= 1:
            return result(single_name, single_key, single_info)
        if aoe_valid:
            return result(aoe_name, aoe_key, aoe_info)
        return result(single_name, single_key, single_info)

    # --- Buff面板 & 计时器 ---

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
        self._save_config()  # 显式保存
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
                        names, coords, methods = MapLoader._load_patrol_routes(map_name, map_dir)
                        if names:
                            self._patrol_route_names = names
                            self._patrol_all_routes = coords
                            self._patrol_waypoints = coords[0] if coords else []
                            self._patrol_return_method = methods[0] if methods else "一直走"
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
        """构建底部 footer：游戏窗口选择、开始按钮、状态"""
        footer = tk.LabelFrame(parent, text="游戏控制",
                                font=("Microsoft YaHei", 10, "bold"),
                                padx=8, pady=5, fg="#2c3e50")
        footer.pack(side="bottom", fill="x", padx=15, pady=(5, 8))

        # 上一行：游戏窗口 + 按钮
        control_row = tk.Frame(footer)
        control_row.pack(fill="x", pady=(0, 4))

        tk.Label(control_row, text="游戏窗口:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        self.window_var = tk.StringVar(value="冒险岛怀旧服")
        self.window_combo = ttk.Combobox(control_row, textvariable=self.window_var,
                                          values=["冒险岛怀旧服"], state="readonly",
                                          width=28, font=("Microsoft YaHei", 9))
        self.window_combo.pack(side="left", padx=(0, 15))
        self.window_combo.bind("<Button-1>", self._on_window_dropdown_click)

        # 角色名（玩家OCR识别定位）
        tk.Label(control_row, text="角色名:",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))
        self.char_name_var = tk.StringVar(value="")
        char_entry = tk.Entry(control_row, textvariable=self.char_name_var,
                              font=("Microsoft YaHei", 9), width=14)
        char_entry.pack(side="left", padx=(0, 15))

        self.btn = tk.Button(control_row, text="开始打怪",
                              font=("Microsoft YaHei", 12, "bold"),
                              width=10, height=1,
                              bg="#e74c3c", fg="white",
                              activebackground="#c0392b",
                              relief="flat", cursor="hand2",
                              command=self.toggle)
        self.btn.pack(side="right")

        # 调试截图开关
        self._debug_var = tk.BooleanVar(value=self._debug_enabled)
        self._debug_cb = tk.Checkbutton(control_row, text="调试截图",
                                         variable=self._debug_var,
                                         font=("Microsoft YaHei", 8), fg="#888",
                                         command=self._on_debug_toggle)
        self._debug_cb.pack(side="right", padx=(0, 10))

        # 下一行：状态
        tk.Label(footer, textvariable=self.status_var,
                 font=("Microsoft YaHei", 9), fg="#555").pack(anchor="w")

    def on_close(self) -> None:
        if self.running:
            self.stop()
        self.root.destroy()

    # ---- 自动药水 ----

    def _build_potion_config(self, parent):
        """构建自动药水配置面板。"""
        panel = tk.LabelFrame(parent, text="自动药水",
                              font=("Microsoft YaHei", 9, "bold"))
        panel.pack(fill="x", padx=8, pady=(2, 0))
        _save = lambda *_: self._save_config()
        self.hp_enabled_var = tk.BooleanVar(value=False)
        self.hp_mode_var = tk.StringVar(value="百分比")
        self.hp_threshold_var = tk.StringVar(value="50")
        self.hp_key_var = tk.StringVar(value="Q")
        self.mp_enabled_var = tk.BooleanVar(value=False)
        self.mp_mode_var = tk.StringVar(value="百分比")
        self.mp_threshold_var = tk.StringVar(value="30")
        self.mp_key_var = tk.StringVar(value="W")
        for var in [self.hp_enabled_var, self.mp_enabled_var,
                    self.hp_mode_var, self.mp_mode_var,
                    self.hp_threshold_var, self.mp_threshold_var,
                    self.hp_key_var, self.mp_key_var]:
            var.trace_add("write", _save)
        potion_key_choices = [d for d, _ in SKILL_KEY_CHOICES]
        f_hp = tk.Frame(panel); f_hp.pack(fill="x", padx=4, pady=2)
        tk.Checkbutton(f_hp, variable=self.hp_enabled_var, text="", width=2).pack(side="left")
        tk.Label(f_hp, text="血量", font=("Microsoft YaHei", 9), width=6, anchor="w").pack(side="left")
        ttk.Combobox(f_hp, textvariable=self.hp_mode_var, values=["百分比", "固定值"],
                     state="readonly", width=6).pack(side="left", padx=2)
        tk.Entry(f_hp, textvariable=self.hp_threshold_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        ttk.Combobox(f_hp, textvariable=self.hp_key_var,
                     values=potion_key_choices, state="readonly",
                     width=6).pack(side="left", padx=2)
        f_mp = tk.Frame(panel); f_mp.pack(fill="x", padx=4, pady=2)
        tk.Checkbutton(f_mp, variable=self.mp_enabled_var, text="", width=2).pack(side="left")
        tk.Label(f_mp, text="蓝量", font=("Microsoft YaHei", 9), width=6, anchor="w").pack(side="left")
        ttk.Combobox(f_mp, textvariable=self.mp_mode_var, values=["百分比", "固定值"],
                     state="readonly", width=6).pack(side="left", padx=2)
        tk.Entry(f_mp, textvariable=self.mp_threshold_var, width=5,
                 font=("Courier", 10)).pack(side="left", padx=2)
        ttk.Combobox(f_mp, textvariable=self.mp_key_var,
                     values=potion_key_choices, state="readonly",
                     width=6).pack(side="left", padx=2)
        # 药水配置缓存恢复统一在 _restore_and_trace_config() 处理

    def _apply_potion_config(self):
        """将 UI 配置同步到 AutoPotion 实例。"""
        if not hasattr(self, 'potion'): return
        self.potion.hp_key = config.SKILL_KEY_LOOKUP.get(self.hp_key_var.get(), 'Q')
        self.potion.hp_enabled = self.hp_enabled_var.get()
        self.potion.hp_mode = self.hp_mode_var.get()
        try: self.potion.hp_threshold = int(self.hp_threshold_var.get())
        except ValueError: pass
        self.potion.mp_enabled = self.mp_enabled_var.get()
        self.potion.mp_mode = self.mp_mode_var.get()
        try: self.potion.mp_threshold = int(self.mp_threshold_var.get())
        except ValueError: pass
        self.potion.mp_key = config.SKILL_KEY_LOOKUP.get(self.mp_key_var.get(), 'W')


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
