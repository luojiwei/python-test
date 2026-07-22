"""commands.py — 命令系统 + 决策引擎"""

import time

import config
from config import (
    CLIMB_TIMEOUT, CLIMB_OVERSHOOT, MOUNT_DURATION, POSITION_THRESHOLD,
    JUMP_TIMEOUT, FLASH_TIMEOUT, MOVE_TIMEOUT,
    ATTACK_DISTANCE, ATTACK_VERTICAL, ATTACK_PULSE,
    PLATFORM_TOLERANCE, JUMP_THRESHOLD,
)
from edge_types import TYPE_CN
from input_utils import KeySender
from key_actions import KeyActionManager
from perception import GameState
from world_model import WorldModel

# ============================================================
# 命令基类
# ============================================================

class Command:
    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        pass

    def is_finished(self) -> bool:
        return False

    def is_transition(self) -> bool:
        return False


# ============================================================
# 攻击类命令
# ============================================================

class AttackCommand(Command):
    def __init__(self) -> None:
        self._last_attack: float = 0.0

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        actions.stop()
        now = time.time()
        if now - self._last_attack >= ATTACK_PULSE:
            actions.attack_tap()
            self._last_attack = now


class TimedAttackCommand(Command):
    """固定路线专用：攻击0.3s后自动结束，由 decide() 重新评估。"""
    def __init__(self) -> None:
        self._attack: AttackCommand = AttackCommand()
        self._end_time: float = time.time() + 0.3

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        self._attack.execute_tick(actions, state, wm)

    def is_finished(self) -> bool:
        return time.time() > self._end_time


class TurnAndAttackCommand(Command):
    def __init__(self, direction: str) -> None:
        self._direction = direction
        self._turned: bool = False
        self._attack = AttackCommand()

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        if not self._turned:
            actions.turn(self._direction)
            self._turned = True
        else:
            self._attack.execute_tick(actions, state, wm)


class MoveToCommand(Command):
    def __init__(self, target_x: float, need_jump: bool = False) -> None:
        self._target_x = target_x
        self._need_jump = need_jump
        self._start_time: float = time.time()

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        dx = self._target_x - state.player_screen_x
        move_dir = 'r' if dx > 0 else 'l'
        if self._need_jump:
            actions.jump(move_dir)
        else:
            actions.move(move_dir)

    def is_finished(self) -> bool:
        return time.time() - self._start_time > MOVE_TIMEOUT


# ============================================================
# 爬梯命令
# ============================================================

class ClimbCommand(Command):
    def __init__(self, direction: str, rope_x: float, target_y: float,
                 departure_y: float = 0.0, timeout: float = CLIMB_TIMEOUT,
                 target_platform: str = ""):
        self._direction = direction
        self._rope_x = rope_x
        self._target_y = target_y
        self._target_platform = target_platform
        self._departure_y = departure_y
        self._timeout = timeout
        self._start_time: float = time.time()
        self._cstate: str = "turn"       # turn → move → mount → climb → finish
        self._turn_time: float = 0.0
        self._settle_time: float = 0.0
        self._reached_top_time: float = 0.0
        self._mount_time: float = 0.0
        self._finish_time: float = 0.0
        self._finished: bool = False

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        px, py = state.player_minimap_x, state.player_minimap_y
        dx = self._rope_x - px
        now = time.time()
        going_up = (self._direction == "up")

        if now - self._start_time > self._timeout:
            actions.release_all()
            self._finished = True
            return

        if self._cstate == "turn":
            move_dir = 'r' if dx > 0 else 'l'
            if state.facing and state.facing != move_dir:
                if self._turn_time == 0.0:
                    self._turn_time = now
                    actions.release_all()
                    actions.tap(move_dir, duration=0.05)
                elif now - self._turn_time > 0.18:
                    self._cstate = "move"
                    state.facing = move_dir
            else:
                self._cstate = "move"

        if self._cstate == "move":
            move_dir = 'r' if dx > 0 else 'l'
            if going_up:
                sign = 1 if move_dir == 'r' else -1
                at_position = abs(dx - sign * 2) <= POSITION_THRESHOLD
            else:
                at_position = abs(dx) <= POSITION_THRESHOLD

            if at_position:
                if self._settle_time == 0.0:
                    self._settle_time = now
                    actions.release_all()
                elif now - self._settle_time > 0.15:
                    self._cstate = "mount"; self._mount_time = now
                    if going_up:
                        actions.jump_up(move_dir)
                    else:
                        actions.climb_down()
            else:
                self._settle_time = 0.0
                actions.move(move_dir)

        elif self._cstate == "mount":
            if going_up:
                if now - self._mount_time > MOUNT_DURATION:
                    self._cstate = "climb"; actions.climb_up()
            else:
                if now - self._mount_time > 0.3:
                    self._cstate = "climb"; actions.climb_down()

        elif self._cstate == "climb":
            if going_up:
                actions.climb_up()
                if py <= self._target_y:
                    if self._reached_top_time == 0.0:
                        self._reached_top_time = now
                    elif now - self._reached_top_time > CLIMB_OVERSHOOT:
                        self._cstate = "finish"; self._finish_time = now
                        actions.release_all()
                else:
                    self._reached_top_time = 0.0
            else:
                actions.climb_down()
                if py >= self._target_y - 3:
                    if self._reached_top_time == 0.0:
                        self._reached_top_time = now
                    elif now - self._reached_top_time > 0.5:
                        self._cstate = "finish"; self._finish_time = now
                        actions.release_all()
                else:
                    self._reached_top_time = 0.0

        elif self._cstate == "finish":
            actions.release_all(); self._finished = True

    def is_finished(self) -> bool:
        return self._finished

    def is_transition(self) -> bool:
        return True

    def is_on_rope(self, py: float) -> bool:
        """判断角色是否还在绳梯范围内"""
        # overshoot 期间角色有意爬过绳梯顶端，仍视为"在绳梯上"
        if self._cstate == "climb" and self._reached_top_time > 0:
            return True
        if self._direction == "up":
            return self._target_y < py < self._departure_y
        else:
            return self._departure_y < py < self._target_y


# ============================================================
# 跳跃 & 闪现
# ============================================================

class JumpCommand(Command):
    def __init__(self, target_x: float, target_y: float,
                 timeout: float = JUMP_TIMEOUT, target_platform: str = ""):
        self._target_x = target_x
        self._target_y = target_y
        self._target_platform = target_platform
        self._timeout = timeout
        self._start_time = time.time()
        self._stage: str = "move"

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        now = time.time()
        if now - self._start_time > self._timeout:
            actions.release_all(); return
        if self._stage == "move":
            dx = self._target_x - state.player_minimap_x
            if abs(dx) <= 3:
                self._stage = "jump"; self._start_time = now
                d = 'r' if dx > 0 else 'l'
                actions.jump(d); time.sleep(0.2); actions.release_all()
            else:
                d = 'r' if dx > 0 else 'l'
                actions.move(d)

    def is_finished(self) -> bool:
        return time.time() - self._start_time > self._timeout

    def is_transition(self) -> bool:
        return True


class FlashCommand(Command):
    def __init__(self, target_x: float, target_y: float,
                 timeout: float = FLASH_TIMEOUT, target_platform: str = ""):
        self._target_x = target_x
        self._target_y = target_y
        self._target_platform = target_platform
        self._timeout = timeout
        self._start_time = time.time()

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        if time.time() - self._start_time > self._timeout:
            actions.release_all(); return
        dx = self._target_x - state.player_minimap_x
        if abs(dx) <= 3:
            actions.release_all()
        else:
            actions.move('r' if dx > 0 else 'l')

    def is_finished(self) -> bool:
        return time.time() - self._start_time > self._timeout

    def is_transition(self) -> bool:
        return True


class HoldDirCommand(Command):
    """固定路线专用：持续朝某方向行走，无超时，由 decide() 定时换向。"""
    def __init__(self, direction: str) -> None:
        self._dir = direction

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        actions.move_no_facing(self._dir)


class IdleCommand(Command):
    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        actions.release_all()


# ============================================================
# 决策引擎
# ============================================================

# TYPE_CN 已移至 edge_types.py，此处保留引用以兼容旧代码
TYPE_CN = TYPE_CN


def nearest_monster(cx: float, cy: float, monsters: list[dict]) -> dict | None:
    on_platform = [m for m in monsters
                   if abs(m["y2"] - cy) <= PLATFORM_TOLERANCE]
    if not on_platform:
        return None
    return min(on_platform, key=lambda m: abs(m["cx"] - cx))


def decide(state: GameState, wm: WorldModel,
           patrol_direction: str,
           transition_in_progress: bool,
           min_monsters_on_platform: int = 3,
           patrol_mode: str = "auto_hunt",
           patrol_waypoints: list | None = None,
           current_waypoint_idx: int = 0) -> tuple[Command, str, int, str]:
    """决策调度器：根据 patrol_mode 分发到对应策略。

    新决策模式只需在 decision_strategies.STRATEGIES 中注册即可。
    """
    from decision_strategies import STRATEGIES
    strategy = STRATEGIES.get(patrol_mode, STRATEGIES.get("auto_hunt"))
    if strategy is None:
        return IdleCommand(), patrol_direction, current_waypoint_idx, "无匹配策略"
    return strategy.decide(
        state, wm, patrol_direction, transition_in_progress,
        min_monsters_on_platform, patrol_waypoints, current_waypoint_idx)
