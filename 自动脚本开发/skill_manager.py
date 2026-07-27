"""skill_manager.py — Buff配置管理器。

封装Buff GUI 面板构建、配置读写、计时器释放、缓存持久化。
从 main.py 中提取约 100 行Buff相关逻辑。
"""

import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from config import PROJECT_DIR, SKILL_KEY_CHOICES, SKILL_KEY_LOOKUP, SKILL_SAFETY_MARGIN


class SkillManager:
    """自动Buff管理器。

    职责：
    - build_panel(): 构建 2 列布局 GUI 面板
    - 从 GUI 读取Buff配置到内部列表
    - 持久化到 skill_config.json
    - 按间隔自动释放Buff
    """

    def __init__(self, actions=None,   # KeyActionManager (按需传入)
                 log_cb=None) -> None:
        self.actions = actions
        self._log = log_cb or (lambda s: None)

        self._rows: list[dict] = []            # 每行的 widget 引用
        self._configs: list[dict] = []          # 运行时配置快照
        self._last_cast: dict[int, float] = {}  # row_index -> 上次释放时间
        self._add_btn: tk.Button | None = None
        self._cols: list[tk.Frame] = []

        # 非阻塞：Buff动画锁定结束时间
        self._animation_locked_until: float = 0.0

    # ---- 属性 ----

    @property
    def configs(self) -> list[dict]:
        return self._configs

    @property
    def rows(self) -> list[dict]:
        return self._rows

    # ---- GUI 构建 ----

    def build_panel(self, parent: tk.Widget) -> tk.LabelFrame:
        """构建 2 列布局的自动Buff面板，返回 panel Frame。"""
        panel = tk.LabelFrame(parent, text="自动技能",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=8, pady=5, fg="#2c3e50")
        panel.pack(side="top", fill="x", padx=15, pady=(8, 2))

        # 两列容器
        cols_frame = tk.Frame(panel)
        cols_frame.pack(fill="x")
        self._cols = [tk.Frame(cols_frame), tk.Frame(cols_frame)]
        self._cols[0].pack(side="left", fill="x", expand=True, anchor="n")
        self._cols[1].pack(side="left", fill="x", expand=True, anchor="n")

        # 每列的小表头
        for col in self._cols:
            hdr = tk.Frame(col)
            tk.Label(hdr, text="✓", width=2,
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left")
            tk.Label(hdr, text="Buff名称", width=10, anchor="w",
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left", padx=2)
            tk.Label(hdr, text="键位", width=7,
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left", padx=2)
            tk.Label(hdr, text="持续(秒)", width=8,
                     font=("Microsoft YaHei", 8, "bold")).pack(side="left", padx=2)
            hdr.pack(anchor="w", pady=(0, 2))

        # 添加按钮
        btn_frame = tk.Frame(panel)
        self._add_btn = tk.Button(btn_frame, text="+ 添加Buff",
                                   font=("Microsoft YaHei", 8),
                                   command=self.add_row)
        self._add_btn.pack(side="left")
        tk.Label(btn_frame, text="  最多10个",
                 font=("Microsoft YaHei", 7), fg="#999").pack(side="left")
        btn_frame.pack(anchor="w", pady=(4, 0))

        return panel

    def add_row(self, name: str = "", key_display: str = "PageUp",
                duration: str = "", enabled: bool = True) -> None:
        """添加一行Buff配置（左右交替）。"""
        if len(self._rows) >= 10:
            return

        col_idx = len(self._rows) % 2
        parent = self._cols[col_idx]

        row_frame = tk.Frame(parent)
        row_frame.pack(fill="x", pady=1)

        enabled_var = tk.BooleanVar(value=enabled)
        tk.Checkbutton(row_frame, variable=enabled_var,
                       onvalue=True, offvalue=False).pack(side="left")

        name_var = tk.StringVar(value=name)
        tk.Entry(row_frame, textvariable=name_var, width=10,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

        key_display_names = [d for d, _ in SKILL_KEY_CHOICES]
        key_var = tk.StringVar(value=key_display)
        key_combo = ttk.Combobox(row_frame, textvariable=key_var,
                                  values=key_display_names,
                                  height=10, width=7, state="readonly")
        key_combo.pack(side="left", padx=2)

        dur_var = tk.StringVar(value=duration)
        tk.Entry(row_frame, textvariable=dur_var, width=5,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        tk.Label(row_frame, text="秒", font=("Microsoft YaHei", 8)).pack(side="left")

        del_btn = tk.Button(row_frame, text="✕", font=("Microsoft YaHei", 9, "bold"),
                             fg="#e74c3c", width=2, relief="flat",
                             command=lambda f=row_frame: self._remove_row(f))
        del_btn.pack(side="left", padx=(6, 0))

        self._rows.append({
            "frame": row_frame,
            "name_var": name_var,
            "key_var": key_var,
            "dur_var": dur_var,
            "enabled_var": enabled_var,
        })

        if len(self._rows) >= 10 and self._add_btn:
            self._add_btn.config(state="disabled")

    def _remove_row(self, row_frame: tk.Frame) -> None:
        for i, r in enumerate(self._rows):
            if r["frame"] is row_frame:
                self._rows.pop(i)
                row_frame.destroy()
                break
        if self._add_btn:
            self._add_btn.config(state="normal")

    # ---- 配置读写 ----

    def read_configs(self) -> list[dict]:
        """从 GUI 读取Buff配置到内部列表并返回。"""
        configs: list[dict] = []
        for row in self._rows:
            name = row["name_var"].get().strip()
            key_display = row["key_var"].get()
            key_name = SKILL_KEY_LOOKUP.get(key_display, "")
            try:
                duration = float(row["dur_var"].get())
            except (ValueError, TypeError):
                duration = 0.0
            configs.append({
                "name": name or f"技能{len(configs)+1}",
                "key": key_name,
                "duration": duration,
                "enabled": row["enabled_var"].get(),
            })
        self._configs = configs
        return configs

    def save_cache(self, map_name: str, patrol_mode: str,
                   route_name: str, min_monsters: int,
                   occupation: str = "", single_skill: str = "",
                   aoe_skill: str = "", skill_rule: str = "mixed",
                   single_skill_key: str = "", aoe_skill_key: str = "") -> None:
        """保存 Buff 配置 + 决策配置 + 职业配置 + 地图到缓存文件。"""
        cache_data = {
            "map": map_name,
            "skills": [],
            "patrol_mode": patrol_mode,
            "route_name": route_name,
            "min_monsters": min_monsters,
            "occupation": occupation,
            "single_skill": single_skill,
            "aoe_skill": aoe_skill,
            "single_skill_key": single_skill_key,
            "aoe_skill_key": aoe_skill_key,
            "skill_rule": skill_rule,
        }
        for row in self._rows:
            cache_data["skills"].append({
                "name": row["name_var"].get(),
                "key_display": row["key_var"].get(),
                "duration": row["dur_var"].get(),
                "enabled": row["enabled_var"].get(),
            })
        try:
            with open(PROJECT_DIR / "skill_config.json", "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def load_cached_skills() -> list[dict]:
        """从缓存文件加载Buff配置列表。"""
        cache_path = PROJECT_DIR / "skill_config.json"
        if not cache_path.exists():
            return []
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return data.get("skills", [])
        except Exception:
            return []

    # ---- 运行时 ----

    def reset_timers(self) -> None:
        """重置所有Buff计时器。"""
        self._last_cast.clear()
        self._animation_locked_until = 0.0

    def initial_cast(self, start_now: float | None = None) -> None:
        """启动时释放所有有效Buff一次。"""
        if start_now is None:
            start_now = time.time()
        for i, cfg in enumerate(self._configs):
            if not cfg.get("enabled", True):
                continue
            key = cfg.get("key", "")
            if key and self.actions:
                self.actions.tap(key, duration=0.05)
                self._last_cast[i] = start_now
                self._log(f"[Buff] {cfg['name']} 初始释放 (键位:{key})")
                time.sleep(1.0)  # 启动初始化阶段可以阻塞

    def process(self, now: float) -> None:
        """检查并释放到期的Buff（非阻塞）。

        使用 _animation_locked_until 替代 time.sleep()，
        避免阻塞 30ms tick 循环。
        """
        if self.actions is None:
            return

        # Buff动画锁定中 → 等待
        if now < self._animation_locked_until:
            return

        for i, cfg in enumerate(self._configs):
            if not cfg.get("enabled", True):
                continue
            key = cfg.get("key", "")
            duration = cfg.get("duration", 0.0)
            if not key or duration <= 0:
                continue

            interval = max(duration - SKILL_SAFETY_MARGIN, 1.0)
            last = self._last_cast.get(i, 0.0)

            if now - last >= interval:
                self.actions.cast_skill(key)
                self._last_cast[i] = now
                self._log(f"[Buff] {cfg['name']} 释放 (键位:{key}, 间隔:{interval:.0f}s)")
                # 非阻塞：记录动画锁定结束时间（替代 time.sleep(0.3)）
                self._animation_locked_until = now + 0.3
                break  # 一次只释放一个Buff
