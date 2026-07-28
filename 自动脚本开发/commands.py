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
        # 绳梯卡住检测
        self._stuck_start_x: float = 0.0       # 进入 climb 时的 x 坐标
        self._stuck_recovering: bool = False     # 是否正在恢复
        self._stuck_recovery_start: float = 0.0  # 恢复开始时间
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
            # === 绳梯卡住检测 ===
            if self._stuck_start_x == 0.0:
                self._stuck_start_x = px  # 记录进入 climb 时的 x

            if self._stuck_recovering:
                # 恢复模式：持续按爬梯方向
                if going_up:
                    actions.climb_up()
                else:
                    actions.climb_down()
                if now - self._stuck_recovery_start > 0.5:
                    self._stuck_recovering = False
                    self._stuck_start_x = px  # 重置检测起点
                    self._log(f"[爬梯] 卡住恢复完成 x={px:.0f}")
                return  # 恢复期间不走正常逻辑

            # 正常检测：检查最近 10 帧坐标是否变化
            history = state.pos_history
            if len(history) >= 10:
                recent = history[-10:]
                all_same = all((x == px and y == py) for x, y in recent)
                if all_same and abs(px - self._stuck_start_x) < 2:
                    # 卡住了！进入恢复模式
                    self._stuck_recovering = True
                    self._stuck_recovery_start = now
                    self._log(f"[爬梯] 检测到绳梯卡死! x={px:.0f} y={py:.0f} — 恢复0.5s")
                    if going_up:
                        actions.climb_up()
                    else:
                        actions.climb_down()
                    return
            elif abs(px - self._stuck_start_x) >= 2:
                # x 有显著位移 → 重置检测起点
                self._stuck_start_x = px

            # 正常爬梯逻辑
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
                elif self._reached_top_time == 0.0:
                    self._reached_top_time = 0.0  # 正常爬梯中，无变化
                # 已开始 overshoot 计时 → 不重置（py 短暂弹动是正常的）
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
           current_waypoint_idx: int = 0,
           return_method: str = "一直走",
           get_skill_cb=None, log_cb=None) -> tuple[Command, str, int, str]:
    """决策调度器。get_skill_cb(monster_count) -> {"name","key","range","fullscreen"}"""
    from decision_strategies import STRATEGIES, create_strategies
    strategies = create_strategies(get_skill_cb, log_cb=log_cb) if get_skill_cb or log_cb else STRATEGIES
    strategy = strategies.get(patrol_mode, strategies.get("auto_hunt"))
    if strategy is None:
        return IdleCommand(), patrol_direction, current_waypoint_idx, "无匹配策略"
    return strategy.decide(
        state, wm, patrol_direction, transition_in_progress,
        min_monsters_on_platform, patrol_waypoints, current_waypoint_idx,
        return_method)
