"""perception_pipeline.py — 感知流水线。

封装主循环中 ~100 行的感知逻辑为一个类，一步完成：
模板匹配(含校准器兜底) → YOLO怪物检测(含节流) → 小地图定位 → 绳梯检测 → 诊断日志。
"""

import time

import numpy as np

from config import YOLO_INTERVAL
from edge_types import EdgeType
from input_utils import capture_minimap
from perception import (
    Calibrator, GameState, detect_monsters, detect_on_rope,
    find_character, find_yellow_dot,
)
from world_model import WorldModel


class PerceptionPipeline:
    """感知流水线：读取一帧，更新 GameState，处理异常。"""

    def __init__(self, calib: Calibrator, template: np.ndarray,
                 search_region: tuple[int, int, int, int],
                 yolo_model, wm: WorldModel,
                 actions,   # KeyActionManager
                 log_cb=None) -> None:
        self.calib = calib
        self.template = template
        self.search_region = search_region
        self.yolo_model = yolo_model
        self.wm = wm
        self.actions = actions
        self._log = log_cb or (lambda s: None)

        self._last_yolo: float = 0.0
        self._monsters: list[dict] = []
        self._char_lost_frames: int = 0

    def perceive(self, frame: np.ndarray, state: GameState,
                 target_hwnd: int, frame_count: int) -> None:
        """处理一帧感知数据，直接更新 state。

        Args:
            frame: 游戏截图 (numpy array, BGR)
            state:  GameState 实例（直接修改）
            target_hwnd: 游戏窗口句柄
            frame_count: 当前帧序号（用于诊断日志）
        """
        now = time.time()

        # ---- 1) 角色屏幕定位 ----
        char = find_character(frame, self.template, self.search_region)
        if char is None:
            self._char_lost_frames += 1
            if self._char_lost_frames >= 5:
                if state.player_minimap_x != 0:
                    self._char_lost_frames = 0
                    if self.calib.has_data():
                        px, py, pred_conf = self.calib.predict(
                            state.player_minimap_x, state.player_minimap_y)
                        if pred_conf > 0.3:
                            state.player_screen_x = px
                            state.player_screen_y = py
                else:
                    self.actions.force_release_all()
                    self._log(f"[{frame_count:04d}] 角色丢失(小地图无信号)")
                    time.sleep(0.2)
        else:
            cx, cy, conf = char
            state.player_screen_x = cx
            state.player_screen_y = cy
            self._char_lost_frames = 0
            if conf > 0.55 and state.player_minimap_x != 0:
                self.calib.add(state.player_minimap_x, state.player_minimap_y, cx, cy)

        # ---- 2) YOLO 怪物检测 ----
        if now - self._last_yolo >= YOLO_INTERVAL:
            try:
                self._monsters = detect_monsters(self.yolo_model, frame)
                self._last_yolo = now
            except Exception as e:
                self._log(f"[{frame_count:04d}] YOLO异常: {e}")
        state.monsters = self._monsters

        # ---- 3) 小地图定位 + 绳梯检测 ----
        mm = capture_minimap(target_hwnd, tuple(self.wm.mm_region))
        dot = None
        if mm is not None:
            dot = find_yellow_dot(mm)
            if dot is not None:
                state.player_minimap_x = dot[0]
                state.player_minimap_y = dot[1]
                pid = self.wm.find_platform(dot[0], dot[1])
                if pid:
                    state.current_platform = pid

                was_on_rope = state.on_rope
                if detect_on_rope(self.wm, dot[0], dot[1]):
                    state.rope_frames += 1
                    if state.rope_frames >= 5 and not state.on_rope:
                        state.on_rope = True
                else:
                    state.rope_frames = 0
                    state.on_rope = False
                if state.on_rope != was_on_rope:
                    if state.on_rope:
                        self._log(f"[{frame_count:04d}] 检测到角色在绳梯上 "
                                  f"(x={dot[0]:.0f}, y={dot[1]:.0f})")
                    else:
                        self._log(f"[{frame_count:04d}] 角色离开绳梯")

        # ---- 4) 诊断日志（每 2 秒） ----
        if frame_count % 60 == 0:
            self._diagnose_rope(mm, dot, state, frame_count)

    def _diagnose_rope(self, mm, dot, state: GameState, frame_count: int) -> None:
        """绳梯检测诊断日志。"""
        if mm is None:
            self._log(f"[{frame_count:04d}] 绳梯诊断: 小地图截图失败")
        elif dot is None:
            self._log(f"[{frame_count:04d}] 绳梯诊断: 黄点未找到 "
                      f"(minimap={self.wm.mm_region})")
        elif not state.on_rope:
            px, py = state.player_minimap_x, state.player_minimap_y
            nearest_rope = ""
            nearest_dist = 9999.0
            for e in self.wm.edges:
                if e.get("type") != EdgeType.ROPE:
                    continue
                rx = float(e.get("top", {}).get("x", 9999))
                ty = float(e.get("top", {}).get("y", 9999))
                by = float(e.get("bottom", {}).get("y", 9999))
                y_min, y_max = sorted([ty, by])
                dx = abs(px - rx)
                if y_min <= py <= y_max and dx < nearest_dist:
                    nearest_dist = dx
                    nearest_rope = f"x={rx:.0f} y=[{y_min:.0f},{y_max:.0f}]"
            self._log(f"[{frame_count:04d}] 绳梯诊断: 黄点有 "
                      f"pos=({px:.0f},{py:.0f})  "
                      f"最近绳梯={nearest_rope or '无匹配'} dist={nearest_dist if nearest_dist < 9999 else '-'}")
