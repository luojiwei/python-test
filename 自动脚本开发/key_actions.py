"""key_actions.py — 语义化按键操作管理器。

在 KeySender 之上封装一层高层操作，职责：
1. 自动同步 GameState.facing（无需每个命令手动更新）
2. 语义化日志（"转向右" 而非 "press r"）
3. 统一按键参数（攻击/转向/技能时长只在一处配置）
"""

import time
from typing import Callable

from perception import GameState


# ============================================================
# 参数配置
# ============================================================

ATTACK_TAP_MS: float = 0.03      # 攻击键轻触时长
TURN_TAP_MS: float = 0.03        # 转向轻触时长
SKILL_PRE_DELAY: float = 0.15    # 技能释放前停键等待
SKILL_TAP_MS: float = 0.05       # 技能键轻触时长
WAKE_TAP_MS: float = 0.05        # 僵死恢复方向轻触
WAKE_ATTACK_DELAY: float = 0.06  # 僵死恢复攻击间隔

DIR_CN: dict[str, str] = {"l": "左", "r": "右", "u": "上", "d": "下"}


# ============================================================
# KeyActionManager
# ============================================================

class KeyActionManager:
    """语义化按键操作管理器。

    持有 KeySender（底层按键）+ GameState（朝向同步），
    所有移动/转向/攻击/技能操作通过此类执行。
    """

    def __init__(self, keys, state: GameState,
                 log_cb: Callable[[str], None] | None = None) -> None:
        from input_utils import KeySender
        self.keys: KeySender = keys
        self.state: GameState = state
        self._log: Callable[[str], None] = log_cb or (lambda msg: None)

    # ---- 基础操作 ----

    def stop(self) -> None:
        """停止所有移动（释放所有键，不改变朝向）。"""
        self.keys.hold_only(())

    def release_all(self) -> None:
        """释放当前按住的所有键。"""
        self.keys.release_all()

    def force_release_all(self) -> None:
        """遍历所有已知键发送 KEYUP，强制释放。"""
        self.keys.force_release_all()

    def tap(self, key: str, duration: float = 0.05) -> None:
        """轻触一个键。"""
        self.keys.tap(key, duration)

    def hold(self, *key_tuple: str) -> None:
        """保持指定键组合，释放其他（不自动更新朝向）。"""
        self.keys.hold_only(key_tuple)

    # ---- 移动与朝向 ----

    def move(self, direction: str) -> None:
        """朝指定方向行走，自动同步朝向状态。"""
        self.keys.hold_only((direction,))
        prev = self.state.facing
        self.state.facing = direction
        if prev != direction:
            self._log(f"转向 {DIR_CN.get(direction, direction)}")

    def move_no_facing(self, direction: str) -> None:
        """朝指定方向行走但不更新朝向（适用于巡逻命令）。"""
        self.keys.hold_only((direction,))

    def turn(self, direction: str) -> None:
        """原地转向：停止后轻触方向键，更新朝向。"""
        self.keys.hold_only(())
        self.keys.tap(direction, duration=TURN_TAP_MS)
        self.state.facing = direction
        self._log(f"原地转向 {DIR_CN.get(direction, direction)}")

    def jump(self, direction: str) -> None:
        """跳跃+方向移动，同步朝向。"""
        self.keys.hold_only(('j', direction))
        self.state.facing = direction
        self._log(f"跳跃 {DIR_CN.get(direction, direction)}")

    def jump_up(self, direction: str) -> None:
        """上跳+方向（用于上绳梯时的 mount 阶段）。"""
        self.keys.hold_only(('j', 'u', direction))
        self.state.facing = direction

    def climb_up(self) -> None:
        """爬梯上升。"""
        self.keys.hold_only(('u',))

    def climb_down(self) -> None:
        """爬梯下降。"""
        self.keys.hold_only(('d',))

    # ---- 攻击 ----

    def attack_tap(self) -> None:
        """轻触攻击键（不停止移动，用于脉冲计时器）。"""
        self.keys.tap('a', duration=ATTACK_TAP_MS)

    # ---- 技能 ----

    def cast_skill(self, key: str) -> None:
        """释放技能：先停所有按键等待游戏响应，再轻触技能键。"""
        self.keys.release_all()
        time.sleep(SKILL_PRE_DELAY)
        self.keys.tap(key, duration=SKILL_TAP_MS)
        self._log(f"释放技能 [{key}]")

    # ---- 僵死恢复 ----

    def wake_up(self) -> None:
        """僵死恢复：重按当前朝向 + 补一次攻击。"""
        facing = self.state.facing
        self.keys.tap(facing, duration=WAKE_TAP_MS)
        time.sleep(WAKE_ATTACK_DELAY)
        self.keys.tap('a', duration=ATTACK_TAP_MS)
        self._log(f"僵死恢复 ({DIR_CN.get(facing, facing)}+攻击)")
