"""commands.py — 命令系统 + 决策引擎"""

import time

import config
from config import (
    CLIMB_TIMEOUT, CLIMB_OVERSHOOT, MOUNT_DURATION, POSITION_THRESHOLD,
    JUMP_TIMEOUT, FLASH_TIMEOUT, MOVE_TIMEOUT,
    ATTACK_DISTANCE, ATTACK_VERTICAL, ATTACK_PULSE,
    PLATFORM_TOLERANCE, JUMP_THRESHOLD,
)
from input_utils import KeySender
from perception import GameState
from world_model import WorldModel

# ============================================================
# 命令基类
# ============================================================

class Command:
    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
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

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        keys.hold_only(())
        now = time.time()
        if now - self._last_attack >= ATTACK_PULSE:
            keys.tap('a', duration=0.03)
            self._last_attack = now


class TimedAttackCommand(Command):
    """固定路线专用：攻击0.3s后自动结束，由 decide() 重新评估。"""
    def __init__(self) -> None:
        self._attack: AttackCommand = AttackCommand()
        self._end_time: float = time.time() + 0.3

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        self._attack.execute_tick(keys, state, wm)

    def is_finished(self) -> bool:
        return time.time() > self._end_time


class TurnAndAttackCommand(Command):
    def __init__(self, direction: str) -> None:
        self._direction = direction
        self._turned: bool = False
        self._attack = AttackCommand()

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        if not self._turned:
            keys.hold_only(())
            keys.tap(self._direction, duration=0.03)
            self._turned = True
            state.facing = self._direction
        else:
            self._attack.execute_tick(keys, state, wm)


class MoveToCommand(Command):
    def __init__(self, target_x: float, need_jump: bool = False) -> None:
        self._target_x = target_x
        self._need_jump = need_jump
        self._start_time: float = time.time()

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        dx = self._target_x - state.player_screen_x
        move_dir = 'r' if dx > 0 else 'l'
        state.facing = move_dir
        keys.hold_only((move_dir, 'j') if self._need_jump else (move_dir,))

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

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        px, py = state.player_minimap_x, state.player_minimap_y
        dx = self._rope_x - px
        now = time.time()
        going_up = (self._direction == "up")

        if now - self._start_time > self._timeout:
            keys.release_all()
            self._finished = True
            return

        if self._cstate == "turn":
            move_dir = 'r' if dx > 0 else 'l'
            if state.facing and state.facing != move_dir:
                if self._turn_time == 0.0:
                    self._turn_time = now
                    keys.release_all()
                    keys.tap(move_dir, duration=0.05)
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
                    keys.release_all()
                elif now - self._settle_time > 0.15:
                    self._cstate = "mount"; self._mount_time = now
                    if going_up:
                        keys.hold_only(('j', 'u', move_dir))
                    else:
                        keys.hold_only(('d',))
            else:
                self._settle_time = 0.0
                keys.hold_only((move_dir,))
            state.facing = move_dir

        elif self._cstate == "mount":
            if going_up:
                if now - self._mount_time > MOUNT_DURATION:
                    self._cstate = "climb"; keys.hold_only(('u',))
            else:
                # 下梯：按住 d 短暂延迟后进入爬降
                if now - self._mount_time > 0.3:
                    self._cstate = "climb"; keys.hold_only(('d',))

        elif self._cstate == "climb":
            if going_up:
                keys.hold_only(('u',))
                if py <= self._target_y:
                    if self._reached_top_time == 0.0:
                        self._reached_top_time = now
                    elif now - self._reached_top_time > CLIMB_OVERSHOOT:
                        self._cstate = "finish"; self._finish_time = now
                        keys.release_all()
                else:
                    self._reached_top_time = 0.0
            else:
                keys.hold_only(('d',))
                if py >= self._target_y - 3:  # 下到绳梯底部
                    if self._reached_top_time == 0.0:
                        self._reached_top_time = now
                    elif now - self._reached_top_time > 0.5:
                        self._cstate = "finish"; self._finish_time = now
                        keys.release_all()
                else:
                    self._reached_top_time = 0.0

        elif self._cstate == "finish":
            keys.release_all(); self._finished = True

    def is_finished(self) -> bool:
        return self._finished

    def is_transition(self) -> bool:
        return True

    def is_on_rope(self, py: float) -> bool:
        """判断角色是否还在绳梯范围内"""
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

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        now = time.time()
        if now - self._start_time > self._timeout:
            keys.release_all(); return
        if self._stage == "move":
            dx = self._target_x - state.player_minimap_x
            if abs(dx) <= 3:
                self._stage = "jump"; self._start_time = now
                d = 'r' if dx > 0 else 'l'
                keys.hold_only(('j', d)); time.sleep(0.2); keys.release_all()
                state.facing = d
            else:
                d = 'r' if dx > 0 else 'l'
                keys.hold_only((d,))
                state.facing = d

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

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        if time.time() - self._start_time > self._timeout:
            keys.release_all(); return
        dx = self._target_x - state.player_minimap_x
        if abs(dx) <= 3:
            keys.release_all()
        else:
            keys.hold_only(('r' if dx > 0 else 'l',))
            state.facing = 'r' if dx > 0 else 'l'

    def is_finished(self) -> bool:
        return time.time() - self._start_time > self._timeout

    def is_transition(self) -> bool:
        return True


class HoldDirCommand(Command):
    """固定路线专用：持续朝某方向行走，无超时，由 decide() 定时换向。"""
    def __init__(self, direction: str) -> None:
        self._dir = direction

    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        keys.hold_only((self._dir,))


class IdleCommand(Command):
    def execute_tick(self, keys: KeySender, state: GameState, wm: WorldModel) -> None:
        keys.release_all()


# ============================================================
# 决策引擎
# ============================================================

TYPE_CN = {"rope": "绳梯", "jump": "跳跃", "flash": "闪现"}


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
    """返回 (command, new_patrol_direction, new_waypoint_idx, log_text)

    patrol_mode:
        "auto_hunt"  → 自动寻怪（现有逻辑，平台没怪才切换）
        "fixed_route"→ 固定路线（按途经点行进，只打前方怪，不转身不追击）
    min_monsters_on_platform: 自动寻怪时，当前平台怪物数少于此值则触发平台切换
    patrol_waypoints: 固定路线途经点列表 [(minimap_x, minimap_y), ...]"""
    cx, cy = state.player_screen_x, state.player_screen_y
    monsters = state.monsters
    facing = state.facing
    current_platform = wm.find_platform(state.player_minimap_x, state.player_minimap_y)
    if current_platform:
        state.current_platform = current_platform

    plat_label = current_platform or "未知"
    log_lines: list[str] = []
    log_lines.append(f"角色: 屏幕({cx:.0f},{cy:.0f})  小地图({state.player_minimap_x:.0f},{state.player_minimap_y:.0f})")
    log_lines.append(f"平台: {plat_label}  朝向: {facing}  模式: {'固定路线' if patrol_mode == 'fixed_route' else '自动寻怪'}")

    # ============================================================
    # 固定路线模式
    # ============================================================
    if patrol_mode == "fixed_route" and patrol_waypoints and len(patrol_waypoints) >= 2:
        # 角色在绳梯上 → 保持当前动作，不检测怪物
        if state.on_rope:
            log_lines.append("动作: 绳梯上，继续当前操作（跳过怪检测）")
            return None, patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 确保索引有效
        if current_waypoint_idx >= len(patrol_waypoints):
            current_waypoint_idx = 0
        wp = patrol_waypoints[current_waypoint_idx]
        wp_x, wp_y = wp

        # 找到途经点所在的平台
        wp_platform = wm.find_platform(wp_x, wp_y)
        plat_label2 = wp_platform or "未知"

        mm_dx = wp_x - state.player_minimap_x
        mm_dy = wp_y - state.player_minimap_y
        log_lines.append(f"途经点[{current_waypoint_idx}]: ({wp_x:.0f},{wp_y:.0f})  "
                         f"平台={plat_label2}  距离=({mm_dx:+.0f},{mm_dy:+.0f})")

        # ---- 跨平台移动（途经点在另一平台） ----
        if current_platform and wp_platform and current_platform != wp_platform:
            if transition_in_progress:
                log_lines.append("动作: 跨平台移动中，等待完成...")
                return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

            # 找从当前平台出发、能到达目标平台的出口
            exit_edge = None
            for e in wm.edges:
                if e.get("from_platform") == current_platform and e.get("to_platform") == wp_platform:
                    exit_edge = e
                    break

            # 没直接找到出口：
            #  目标在上方 → 搜索向上出口（绳梯/跳跃/闪现）
            #  目标在下方 → 不搜索，直接步行（角色掉下去自然到达）
            if exit_edge is None:
                curr_order = wm._platform_order(current_platform)
                wp_order = wm._platform_order(wp_platform)
                if wp_order > curr_order:
                    exit_edge = wm.find_nearest_exit(current_platform, "up", state.player_minimap_x)
                else:
                    move_dir = 'r' if mm_dx > 0 else 'l'
                    log_lines.append(f"动作: 目标平台在下方，朝{'右' if move_dir == 'r' else '左'}步行掉落")
                    return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

            if exit_edge is None:
                # 无直接出口，继续朝途经点方向行进
                move_dir = 'r' if mm_dx > 0 else 'l'
                log_lines.append(f"动作: 无直达出口，朝{'右' if move_dir == 'r' else '左'}方向步行")
                return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

            exit_x = wm.get_exit_minimap_x(exit_edge)
            target_y = wm.get_exit_target_y(exit_edge) or 0
            edge_type = exit_edge["type"]
            type_cn = TYPE_CN.get(edge_type, "移动")
            log_lines.append(f"动作: 跨平台 {type_cn} → {wp_platform}")

            if edge_type == "rope":
                d = exit_edge.get("direction", "up")
                dep_y = 0.0
                for p in wm.platforms:
                    if p["id"] == current_platform:
                        dep_y = float(p.get("avg_y", 0))
                        break
                cmd: Command = ClimbCommand(d, exit_x, target_y or 0, dep_y,
                                             target_platform=exit_edge["to_platform"])
            elif edge_type == "jump":
                cmd = JumpCommand(exit_x, target_y or 0, target_platform=exit_edge["to_platform"])
            elif edge_type == "flash":
                cmd = FlashCommand(exit_x, target_y or 0, target_platform=exit_edge["to_platform"])
            else:
                cmd = IdleCommand()
            return cmd, patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # ---- 同平台移动 ----
        if mm_dist := ((mm_dx)**2 + (mm_dy)**2)**0.5 < 10:
            new_idx = (current_waypoint_idx + 1) % len(patrol_waypoints)
            log_lines.append(f"动作: 到达途经点{current_waypoint_idx}，前往{new_idx}")
            return IdleCommand(), patrol_direction, new_idx, "\n".join(log_lines)

        # 攻击前方怪
        for m in monsters:
            dx = m["cx"] - cx
            dy_foot = m["y2"] - cy
            in_range = abs(dx) < ATTACK_DISTANCE and abs(dy_foot) < ATTACK_VERTICAL
            same_dir = (dx >= 0) == (facing == 'r')
            if in_range and same_dir:
                log_lines.append(f"动作: 前方有怪（dx={dx:.0f}），原地攻击")
                return TimedAttackCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 同平台步行
        move_dir = 'r' if mm_dx > 0 else 'l'
        log_lines.append(f"动作: 同平台向{'右' if move_dir == 'r' else '左'}步行")
        return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    # ============================================================
    # 自动寻怪模式（原逻辑）
    # ============================================================
    if transition_in_progress:
        log_lines.append("动作: 平台移动中，等待完成...")
        return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    # 角色在绳梯上 → 沿巡逻方向找最近绳梯边继续爬
    # 不依赖 current_platform（玩家可能处于平台间空隙，find_platform 返回 None）
    if state.on_rope:
        # 在 wm.edges 里找当前坐标最近且能朝巡逻方向走的绳梯
        candidate = None
        for e in wm.edges:
            if e.get("type") != "rope":
                continue
            if patrol_direction == "up" and e.get("direction") != "up":
                continue
            if patrol_direction == "down" and e.get("direction") != "down":
                continue
            top = e.get("top", {})
            bottom = e.get("bottom", {})
            rx = float(top.get("x", 9999))
            ty = float(top.get("y", 9999))
            by = float(bottom.get("y", 9999))
            y_min, y_max = sorted([ty, by])
            if abs(state.player_minimap_x - rx) > 8:
                continue
            if not (y_min <= state.player_minimap_y <= y_max):
                continue
            candidate = e
            break
        if candidate is not None:
            d = candidate.get("direction", patrol_direction)
            exit_x = wm.get_exit_minimap_x(candidate)
            target_y = wm.get_exit_target_y(candidate) or 0
            dep_y = state.player_minimap_y
            if current_platform:
                for p in wm.platforms:
                    if p["id"] == current_platform:
                        dep_y = float(p.get("avg_y", 0))
                        break
            log_lines.append(f"动作: 在绳梯上 (x={state.player_minimap_x:.0f}, y={state.player_minimap_y:.0f})，"
                             f"继续向{'上' if d == 'up' else '下'}爬升")
            return ClimbCommand(d, exit_x, target_y, dep_y,
                                target_platform=candidate["to_platform"]), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
        else:
            log_lines.append("动作: 在绳梯上但未找到匹配绳梯边，待机")
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    on_plat = [m for m in monsters if abs(m["y2"] - cy) <= PLATFORM_TOLERANCE]
    diff_plat = len(monsters) - len(on_plat)
    log_lines.append(f"怪物: 视野{len(monsters)}只 (同平台{len(on_plat)} / 其他{diff_plat})  "
                     f"阈值={min_monsters_on_platform}")

    for i, m in enumerate(on_plat[:8]):
        nm_cn = config.CLASS_NAMES.get(m.get("cls", 99), "?")
        dx = m["cx"] - cx
        dy = m["y2"] - cy
        in_range = abs(dx) < ATTACK_DISTANCE and abs(dy) < ATTACK_VERTICAL
        same_dir = (dx >= 0) == (facing == 'r')
        dist = ((dx)**2 + (dy)**2)**0.5
        status = []
        if in_range and same_dir:
            status.append("同向攻击范围")
        elif in_range:
            status.append("反向攻击范围")
        elif abs(dy) <= PLATFORM_TOLERANCE:
            status.append("同平台待追")
        else:
            status.append("不同平台")
        log_lines.append(f"  #{i} {nm_cn} 中心({m['cx']:.0f},{m['cy']:.0f}) "
                         f"dx={dx:+.0f} dy={dy:+.0f} 距离={dist:.0f} {'|'.join(status)}")

    # 打怪决策（仅当同平台怪物数 ≥ 阈值时执行）
    if len(on_plat) >= min_monsters_on_platform:
        nm = nearest_monster(cx, cy, monsters)
        if nm is not None:
            dx = nm["cx"] - cx
            dy_foot = nm["y2"] - cy
            in_range = abs(dx) < ATTACK_DISTANCE and abs(dy_foot) < ATTACK_VERTICAL
            same_dir = (dx >= 0) == (facing == 'r')

            # 统计前方/后方攻击范围内的怪物数
            front_count = sum(1 for m in on_plat
                              if abs(m["cx"] - cx) < ATTACK_DISTANCE and abs(m["y2"] - cy) < ATTACK_VERTICAL
                              and ((m["cx"] - cx >= 0) == (facing == 'r')))
            back_count = sum(1 for m in on_plat
                             if abs(m["cx"] - cx) < ATTACK_DISTANCE and abs(m["y2"] - cy) < ATTACK_VERTICAL
                             and ((m["cx"] - cx >= 0) != (facing == 'r')))
            total_close = front_count + back_count

            if in_range and same_dir:
                log_lines.append(f"动作: 正前方攻击距离内有{total_close}只怪物，攻击")
                return AttackCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
            elif in_range:
                new_facing = 'r' if nm["cx"] > cx else 'l'
                log_lines.append(f"动作: 身后有{total_close}只怪物，转身追击（转向{new_facing}）")
                return TurnAndAttackCommand(new_facing), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
            else:
                need_jump = dy_foot < JUMP_THRESHOLD
                jmp = " +跳跃" if need_jump else ""
                log_lines.append(f"动作: 追踪最近怪物（距离{((dx)**2+(dy_foot)**2)**0.5:.0f}px）{jmp}")
                return MoveToCommand(nm["cx"], need_jump=need_jump), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
    else:
        log_lines.append(f"动作: 同平台仅{len(on_plat)}只怪物(阈值{min_monsters_on_platform})，切换平台")

    # 寻路决策
    if current_platform is None:
        log_lines.append("动作: 待机（未检测到所在平台）")
        return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    if wm.is_top(current_platform):
        patrol_direction = "down"
        log_lines.append("平台: 已到达顶层，改为向下巡逻")
    elif wm.is_bottom(current_platform):
        patrol_direction = "up"
        log_lines.append("平台: 已到达底层，改为向上巡逻")

    exit_edge = wm.find_nearest_exit(current_platform, patrol_direction, state.player_minimap_x)
    if exit_edge is None:
        direction_cn = "上" if patrol_direction == "up" else "下"
        log_lines.append(f"动作: 当前平台无怪物，无{direction_cn}方出口，待机")
        return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    exit_x = wm.get_exit_minimap_x(exit_edge)
    target_y = wm.get_exit_target_y(exit_edge)
    edge_type = exit_edge["type"]
    type_cn = TYPE_CN.get(edge_type, "移动")

    direction_cn = "上" if patrol_direction == "up" else "下"
    log_lines.append(f"动作: 当前平台无怪物，向{direction_cn}巡逻")
    if edge_type == "rope":
        log_lines.append(f"      到达 小地图x={int(exit_x)} 位置，进行{type_cn}操作")
    else:
        log_lines.append(f"      到达 小地图x={int(exit_x)} 位置，进行{type_cn}操作（→{exit_edge['to_platform']}）")

    if edge_type == "rope":
        d = exit_edge.get("direction", "up")
        dep_y = 0.0
        for p in wm.platforms:
            if p["id"] == current_platform:
                dep_y = float(p.get("avg_y", 0))
                break
        cmd: Command = ClimbCommand(d, exit_x, target_y or 0, dep_y,
                                     target_platform=exit_edge["to_platform"])
    elif edge_type == "jump":
        cmd = JumpCommand(exit_x, target_y or 0, target_platform=exit_edge["to_platform"])
    elif edge_type == "flash":
        cmd = FlashCommand(exit_x, target_y or 0, target_platform=exit_edge["to_platform"])
    else:
        cmd = IdleCommand()
        log_lines.append("动作: 待机（未知边类型）")

    return cmd, patrol_direction, current_waypoint_idx, "\n".join(log_lines)
