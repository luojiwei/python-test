"""commands.py — 命令系统（命令类定义）

决策引擎已迁移到 decision_strategies.py，绳梯卡死监控已迁移到 transition.py。
本文件只保留命令类定义。
"""

import time

import config
from config import (
    CLIMB_TIMEOUT, CLIMB_OVERSHOOT, MOUNT_DURATION, POSITION_THRESHOLD,
    JUMP_TIMEOUT, FLASH_TIMEOUT, MOVE_TIMEOUT,
    ATTACK_DISTANCE, ATTACK_VERTICAL, ATTACK_PULSE,
    PLATFORM_TOLERANCE, JUMP_THRESHOLD,
)
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
    def __init__(self, skill_key: str = 'a') -> None:
        self._key: str = skill_key
        self._last_attack: float = 0.0

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        actions.stop()
        now = time.time()
        if now - self._last_attack >= ATTACK_PULSE:
            actions.attack_tap(self._key)
            self._last_attack = now


class TimedAttackCommand(Command):
    """固定路线专用：攻击0.3s后自动结束，由 decide() 重新评估。"""
    def __init__(self, skill_key: str = 'a') -> None:
        self._attack: AttackCommand = AttackCommand(skill_key)
        self._end_time: float = time.time() + 0.3

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        self._attack.execute_tick(actions, state, wm)

    def is_finished(self) -> bool:
        return time.time() > self._end_time


class TurnAndAttackCommand(Command):
    def __init__(self, direction: str, skill_key: str = 'a') -> None:
        self._direction = direction
        self._turned: bool = False
        self._attack = AttackCommand(skill_key)

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
                 target_platform: str = "", log_cb=None):
        self._direction = direction
        self._rope_x = rope_x
        self._target_y = target_y
        self._target_platform = target_platform
        self._departure_y = departure_y
        self._timeout = timeout
        self._log = log_cb or (lambda s: None)
        self._last_move_log: float = 0.0   # 移动日志节流
        self._start_time: float = time.time()
        self._cstate: str = "turn"       # turn → move → mount → climb → finish
        self._turn_time: float = 0.0
        self._settle_time: float = 0.0
        self._reached_top_time: float = 0.0
        self._mount_time: float = 0.0
        self._finish_time: float = 0.0
        self._finished: bool = False
        self._log(f"[爬梯] 创建: dir={direction} rope_x={rope_x:.0f} target_y={target_y:.0f} dep_y={departure_y:.0f}")

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
            move_dir = 'r' if dx >= 0 else 'l'
            if state.facing and state.facing != move_dir:
                if self._turn_time == 0.0:
                    self._turn_time = now
                    self._log(f"[爬梯] turn: dx={dx:.0f} facing={state.facing} → tap {move_dir}")
                    actions.release_all()
                    actions.tap(move_dir, duration=0.05)
                elif now - self._turn_time > 0.18:
                    self._cstate = "move"
                    self._log(f"[爬梯] turn→move")
                    state.facing = move_dir
            else:
                self._cstate = "move"
                self._log(f"[爬梯] turn: dx={dx:.0f} facing={state.facing} matched → skip to move")

        if self._cstate == "move":
            move_dir = 'r' if dx >= 0 else 'l'
            if going_up:
                sign = 1 if move_dir == 'r' else -1
                at_position = abs(dx - sign * 2) <= POSITION_THRESHOLD
            else:
                at_position = abs(dx) <= POSITION_THRESHOLD

            if at_position:
                if self._settle_time == 0.0:
                    self._settle_time = now
                    self._log(f"[爬梯] move: at_position dx={dx:.0f} → settle")
                    actions.release_all()
                elif now - self._settle_time > 0.15:
                    self._cstate = "mount"; self._mount_time = now
                    self._log(f"[爬梯] move→mount jump_up({move_dir})")
                    if going_up:
                        actions.jump_up(move_dir)
                    else:
                        actions.climb_down()
            else:
                self._settle_time = 0.0
                if now - self._last_move_log >= 1.0:
                    self._log(f"[爬梯] move: dx={dx:.0f} → walk {move_dir}")
                    self._last_move_log = now
                actions.move(move_dir)

        elif self._cstate == "mount":
            if going_up:
                if now - self._mount_time > MOUNT_DURATION:
                    self._cstate = "climb"; actions.climb_up()
                    self._log(f"[爬梯] mount→climb")
            else:
                if now - self._mount_time > 0.3:
                    self._cstate = "climb"; actions.climb_down()

        elif self._cstate == "climb":
            if going_up:
                actions.climb_up()
                if py <= self._target_y:
                    if self._reached_top_time == 0.0:
                        self._reached_top_time = now
                        self._log(f"[爬梯] climb: reached top py={py:.0f} target={self._target_y:.0f}")
                    elif now - self._reached_top_time > CLIMB_OVERSHOOT:
                        self._cstate = "finish"; self._finish_time = now
                        self._log(f"[爬梯] climb→finish")
                        actions.release_all()
                elif self._reached_top_time > 0:
                    self._reached_top_time = 0.0  # py 弹回，重置 overshoot
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
        if self._cstate == "climb" and self._reached_top_time > 0:
            return True  # overshoot 期间仍视为在绳梯上
        if self._direction == "up":
            return self._target_y < py < self._departure_y
        else:
            return self._departure_y < py < self._target_y

    def is_in_climb_state(self) -> bool:
        """是否已进入 climb 阶段（用于绳梯卡住监控器激活判断）"""
        return self._cstate == "climb"

    def is_in_turn_or_move_stage(self) -> bool:
        """是否处于 turn 或 move 阶段（用于 TransitionController 判断是否中断爬梯移动）"""
        return self._cstate in ("turn", "move")

    def get_rope_info(self) -> tuple[float, str]:
        """返回 (rope_x, direction)，供监控器使用"""
        return self._rope_x, self._direction


# ============================================================
# 跳跃 & 闪现
# ============================================================

class JumpCommand(Command):
    """跳跃命令：走到目标x后跳跃，使用阶段计时器替代阻塞sleep。

    阶段: move → jump(空中滞留) → land → finish
    """
    JUMP_AIR_DURATION: float = 0.2  # 跳跃滞空时间

    def __init__(self, target_x: float, target_y: float,
                 timeout: float = JUMP_TIMEOUT, target_platform: str = ""):
        self._target_x = target_x
        self._target_y = target_y
        self._target_platform = target_platform
        self._timeout = timeout
        self._start_time = time.time()
        self._stage: str = "move"
        self._jump_time: float = 0.0  # 跳跃开始时间

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        now = time.time()
        if now - self._start_time > self._timeout:
            actions.release_all(); return

        if self._stage == "move":
            dx = self._target_x - state.player_minimap_x
            if abs(dx) <= 3:
                self._stage = "jump"
                self._jump_time = now
                d = 'r' if dx > 0 else 'l'
                actions.jump(d)
            else:
                d = 'r' if dx > 0 else 'l'
                actions.move(d)

        elif self._stage == "jump":
            # 滞空阶段：等待 JUMP_AIR_DURATION 后释放按键
            if now - self._jump_time >= self.JUMP_AIR_DURATION:
                actions.release_all()
                self._stage = "land"

        elif self._stage == "land":
            # 着陆后标记完成（is_finished 通过超时判断）
            self._stage = "finish"

    def is_finished(self) -> bool:
        return self._stage == "finish" or time.time() - self._start_time > self._timeout

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


class JumpDownCommand(Command):
    """下跳命令：Alt+↓ 从平台跳下。

    固定路线回归方式「下跳」专用，按住 Alt+↓ 短暂时间后释放。
    """
    JUMP_DOWN_DURATION: float = 0.5

    def __init__(self) -> None:
        self._start_time: float = time.time()

    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        if time.time() - self._start_time < self.JUMP_DOWN_DURATION:
            actions.hold('j', 'd')
        else:
            actions.release_all()

    def is_finished(self) -> bool:
        return time.time() - self._start_time > self.JUMP_DOWN_DURATION

    def is_transition(self) -> bool:
        return True


class IdleCommand(Command):
    def execute_tick(self, actions: KeyActionManager, state: GameState, wm: WorldModel) -> None:
        actions.release_all()




