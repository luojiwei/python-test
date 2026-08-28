"""perception_pipeline.py — 感知流水线。

封装主循环中感知逻辑为一个类，一步完成：
YOLO检测(怪物+玩家) → OCR角色定位(玩家昵称模糊匹配) → 小地图定位 → 绳梯检测 → 诊断日志。
"""

import time

import numpy as np

import config
from config import YOLO_INTERVAL, CHARACTER_NAME
from edge_types import EdgeType
from input_utils import capture_minimap
from minimap_tools import compute_world_pos, locate_in_full_map
from perception import (
    Calibrator, GameState, detect_objects, detect_on_rope,
    find_character_by_ocr, find_yellow_dot,
)
from world_model import WorldModel


class PerceptionPipeline:
    """感知流水线：读取一帧，更新 GameState，处理异常。"""

    def __init__(self, calib: Calibrator,
                 yolo_model, wm: WorldModel,
                 actions,   # KeyActionManager
                 dot_hsv_lower: np.ndarray | None = None,
                 dot_hsv_upper: np.ndarray | None = None,
                 char_name: str | None = None,
                 minimap_full: np.ndarray | None = None,
                 minimap_size: tuple[int, int] = (0, 0),
                 log_cb=None) -> None:
        self.calib = calib
        self.yolo_model = yolo_model
        self.wm = wm
        self.actions = actions
        self.dot_hsv_lower = dot_hsv_lower
        self.dot_hsv_upper = dot_hsv_upper
        self.char_name = (char_name if char_name is not None
                          else CHARACTER_NAME)
        self.minimap_full = minimap_full          # 拼接完整小地图（None=整图模式）
        self.minimap_size = minimap_size
        self._log = log_cb or (lambda s: None)

        self._last_yolo: float = 0.0
        self._monsters: list[dict] = []
        self._char: tuple[int, int, float] | None = None  # 最近一次 OCR 角色定位结果
        self._char_lost_frames: int = 0
        self._calib_predict_frames: int = 0   # 连续使用校准器推算屏幕坐标的帧数

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

        # ---- 1) YOLO 推理（节流）：怪物 + 玩家，玩家框用于 OCR 角色定位 ----
        if now - self._last_yolo >= YOLO_INTERVAL:
            try:
                monsters, players = detect_objects(self.yolo_model, frame)
                self._last_yolo = now
                self._monsters = monsters
                if self.char_name:
                    self._char = find_character_by_ocr(frame, players, self.char_name)
                else:
                    self._char = None
            except Exception as e:
                self._log(f"[{frame_count:04d}] YOLO异常: {e}")
        state.monsters = self._monsters

        # ---- 2) 角色屏幕定位（OCR 结果应用 + 校准器兜底） ----
        char = self._char
        if char is None:
            self._char_lost_frames += 1
            if self._char_lost_frames >= 5:
                self._char_lost_frames = 0
                if state.player_minimap_x != 0:
                    # OCR 丢失但小地图有信号：只用校准器推算屏幕坐标。
                    # 注意：屏幕坐标仅用于攻击瞄准，导航/步行只依赖小地图坐标，
                    # 因此这里绝不松键、绝不跳过小地图定位，避免"决策步行但角色不动"。
                    self._calib_predict_frames += 1
                    if self.calib.has_data():
                        px, py, pred_conf = self.calib.predict(
                            state.player_minimap_x, state.player_minimap_y)
                        if pred_conf > 0.3:
                            state.player_screen_x = px
                            state.player_screen_y = py
                    # 推算超限仅提示，不中断移动
                    if self._calib_predict_frames % 30 == 0:
                        self._log(f"[{frame_count:04d}] OCR角色定位持续丢失"
                                  f"{self._calib_predict_frames}帧(小地图定位正常)")
                else:
                    # 小地图也无信号：角色完全丢失，松键等待
                    self.actions.force_release_all()
                    self._log(f"[{frame_count:04d}] 角色丢失(小地图无信号)")
                    time.sleep(0.2)
        else:
            cx, cy, conf = char
            state.player_screen_x = cx
            state.player_screen_y = cy
            self._char_lost_frames = 0
            self._calib_predict_frames = 0  # OCR 定位成功，重置推算计数
            if conf > 0.55 and state.player_minimap_x != 0:
                self.calib.add(state.player_minimap_x, state.player_minimap_y, cx, cy)

        # ---- 3) 小地图定位 + 绳梯检测 ----
        mm = capture_minimap(target_hwnd, tuple(self.wm.mm_region))
        dot = None
        if mm is not None:
            dot = find_yellow_dot(mm, self.dot_hsv_lower, self.dot_hsv_upper)
            if dot is not None:
                # 世界坐标：整图模式=黄点位置；局部滚动模式=完整图定位+黄点
                wx, wy = dot[0], dot[1]
                if self.minimap_full is not None:
                    loc = locate_in_full_map(mm, self.minimap_full)
                    if loc is not None:
                        fh, fw = self.minimap_full.shape[:2]
                        wx, wy = compute_world_pos(dot, loc, (fw, fh),
                                                   self.minimap_size)
                    # 定位失败：保持上一帧坐标，不更新
                    else:
                        wx = state.player_minimap_x
                        wy = state.player_minimap_y
                state.player_minimap_x = wx
                state.player_minimap_y = wy
                pid = self.wm.find_platform(wx, wy)
                state.current_platform = pid  # 允许为 None，防止残留旧平台
                state.last_perception_time = now  # 黄点定位成功，记录感知时间

                was_on_rope = state.on_rope
                if detect_on_rope(self.wm, wx, wy):
                    state.rope_frames += 1
                    if state.rope_frames >= 5 and not state.on_rope:
                        state.on_rope = True
                else:
                    state.rope_frames = 0
                    state.on_rope = False
                if state.on_rope != was_on_rope:
                    if state.on_rope:
                        self._log(f"[{frame_count:04d}] 检测到角色在绳梯上 "
                                  f"(x={wx:.0f}, y={wy:.0f})")
                    else:
                        self._log(f"[{frame_count:04d}] 角色离开绳梯")

        # 感知完成：如果没有黄点，也记录感知时间（说明 YOLO 和模板仍在工作）
        if state.last_perception_time == 0 or now - state.last_perception_time > 0.05:
            state.last_perception_time = now

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
