"""decision_strategies.py — 决策策略模式。

将 commands.py 中 290 行 decide() 重构为策略模式：
- DecisionStrategy: ABC 基类，定义 decide() 接口
- AutoHuntStrategy: 自动寻怪（平台没怪时切换平台）
- FixedRouteStrategy: 固定路线（按途经点行进）
"""

import time
from abc import ABC, abstractmethod

import config
from config import ATTACK_DISTANCE, ATTACK_VERTICAL, JUMP_THRESHOLD, PLATFORM_TOLERANCE
from edge_types import EdgeType, TYPE_CN
from perception import GameState
from world_model import WorldModel

# 延迟导入 Command 子类（避免循环依赖）
from commands import (
    AttackCommand, ClimbCommand, Command, FlashCommand, HoldDirCommand,
    IdleCommand, JumpCommand, JumpDownCommand, MoveToCommand, TimedAttackCommand,
    TurnAndAttackCommand,
)


# ============================================================
# 辅助函数（原 commands.py 中迁移至此）
# ============================================================

def nearest_monster(cx: float, cy: float, monsters: list[dict]) -> dict | None:
    """在怪物列表中找到同平台最近的怪物。"""
    on_platform = [m for m in monsters
                   if abs(m["y2"] - cy) <= PLATFORM_TOLERANCE]
    if not on_platform:
        return None
    return min(on_platform, key=lambda m: abs(m["cx"] - cx))


# ============================================================
# 基类
# ============================================================

class DecisionStrategy(ABC):
    """决策策略基类。"""

    # 感知数据过期阈值：超过此时间视为"感知过期"，降级为 IdleCommand
    PERCEPTION_STALE_THRESHOLD: float = 2.0  # 秒

    def __init__(self, mode_name: str, mode_cn: str,
                 get_skill_cb=None, log_cb=None) -> None:
        self.mode_name = mode_name
        self.mode_cn = mode_cn
        self._get_skill = get_skill_cb or (lambda mc: {"name": None, "key": "a", "range": ATTACK_DISTANCE, "fullscreen": False})
        self._log = log_cb or (lambda msg: None)

    def _monster_name(self, cls_id: int) -> str:
        """根据 YOLO class_id 获取怪物中文名。"""
        return config.CLASS_NAMES.get(cls_id, f"cls_{cls_id}")

    def _check_perception_freshness(self, state: GameState, log_lines: list[str]) -> bool:
        """检查感知数据是否新鲜。

        如果 last_perception_time 超过阈值，说明感知数据过期，
        决策应降级为 IdleCommand 避免基于旧数据做出错误决策。

        Returns:
            True = 感知新鲜，可继续决策
            False = 感知过期，应降级为 IdleCommand
        """
        if state.last_perception_time <= 0:
            # 首帧还没感知过，允许继续
            return True
        age = time.time() - state.last_perception_time
        if age > self.PERCEPTION_STALE_THRESHOLD:
            log_lines.append(f"⚠ 感知过期 ({age:.1f}s > {self.PERCEPTION_STALE_THRESHOLD}s)，降级待机")
            return False
        return True

    @staticmethod
    def _format_monsters(monsters: list[dict], px: float, py: float) -> str:
        """格式化怪物列表为日志字符串：'#只: [名称(sx,sy) ...]'"""
        items = [f"{config.CLASS_NAMES.get(m.get('cls', -1), '?')}"
                 f"({m['cx']:.0f},{m['y2']:.0f})" for m in monsters]
        return f"{len(monsters)}只: [{', '.join(items)}]"

    @abstractmethod
    def decide(self, state: GameState, wm: WorldModel,
               patrol_direction: str,
               transition_in_progress: bool,
               min_monsters_on_platform: int,
               patrol_waypoints: list | None,
               current_waypoint_idx: int,
               return_method: str = "一直走") -> tuple[Command | None, str, int, str]:
        """返回 (command, new_patrol_direction, new_waypoint_idx, log_text)。"""
        ...

    def _handle_on_rope(self, state: GameState, wm: WorldModel,
                         patrol_direction: str,
                         current_waypoint_idx: int,
                         log_lines: list[str]) -> tuple[Command, str, int, str]:
        """绳梯上逻辑：找最近绳梯边继续爬。"""
        candidate = None
        for e in wm.edges:
            if e.get("type") != EdgeType.ROPE:
                continue
            if patrol_direction == "up" and e.get("direction") != "up":
                continue
            if patrol_direction == "down" and e.get("direction") != "down":
                continue
            rx = float(e.get("top", {}).get("x", 9999))
            ty = float(e.get("top", {}).get("y", 9999))
            by = float(e.get("bottom", {}).get("y", 9999))
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
            current_platform = state.current_platform
            if current_platform:
                for p in wm.platforms:
                    if p["id"] == current_platform:
                        dep_y = float(p.get("avg_y", 0))
                        break
            d_cn = "上" if d == "up" else "下"
            log_lines.append(f"动作: 在绳梯上 (x={state.player_minimap_x:.0f}, y={state.player_minimap_y:.0f})，"
                             f"继续向{d_cn}爬升")
            return ClimbCommand(d, exit_x, target_y, dep_y,
                                target_platform=candidate["to_platform"],
                                log_cb=self._log), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
        else:
            log_lines.append("动作: 在绳梯上但未找到匹配绳梯边，待机")
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    def _init_log(self, state: GameState, wm: WorldModel) -> tuple[float, float, list[str], str | None, str]:
        """公共初始化：提取当前状态、生成日志头。

        注意：不调用 wm.find_platform()，state.current_platform 由 PerceptionPipeline 维护。
        """
        cx, cy = state.player_screen_x, state.player_screen_y
        current_platform = state.current_platform

        log_lines: list[str] = []
        plat_label = current_platform or "未知"
        log_lines.append(f"角色: 屏幕({cx:.0f},{cy:.0f})  小地图({state.player_minimap_x:.0f},{state.player_minimap_y:.0f})")
        log_lines.append(f"平台: {plat_label}  朝向: {state.facing}  模式: {self.mode_cn}")

        return cx, cy, log_lines, current_platform, state.facing

    def _monster_log(self, log_lines: list[str], monsters: list[dict],
                     cx: float, cy: float, facing: str) -> list[dict]:
        """怪物统计日志 + 返回同平台怪物列表。"""
        on_plat = [m for m in monsters if abs(m["y2"] - cy) <= PLATFORM_TOLERANCE]
        diff_plat = len(monsters) - len(on_plat)
        skill = self._get_skill(len(on_plat))
        attack_range = float("inf") if skill.get("fullscreen") else skill.get("range", ATTACK_DISTANCE)
        log_lines.append(f"怪物: 视野{len(monsters)}只 (同平台{len(on_plat)} / 其他{diff_plat}) "
                         f"技能={skill.get('name','基础攻击')} 范围={attack_range}px")

        for i, m in enumerate(on_plat[:8]):
            nm_cn = config.CLASS_NAMES.get(m.get("cls", 99), "?")
            dx = m["cx"] - cx
            dy = m["y2"] - cy
            in_range = abs(dx) < attack_range and abs(dy) < ATTACK_VERTICAL
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
        return on_plat

    @staticmethod
    def _create_cmd_from_edge(edge: dict, wm: WorldModel,
                               current_platform: str, log_cb=None) -> Command:
        """从边数据创建对应的 Command。"""
        exit_x = wm.get_exit_minimap_x(edge)
        target_y = wm.get_exit_target_y(edge) or 0
        edge_type = edge["type"]

        if edge_type == EdgeType.ROPE:
            d = edge.get("direction", "up")
            dep_y = 0.0
            for p in wm.platforms:
                if p["id"] == current_platform:
                    dep_y = float(p.get("avg_y", 0))
                    break
            return ClimbCommand(d, exit_x, target_y, dep_y,
                                target_platform=edge["to_platform"],
                                log_cb=log_cb)
        elif edge_type == EdgeType.JUMP:
            return JumpCommand(exit_x, target_y, target_platform=edge["to_platform"])
        elif edge_type == EdgeType.FLASH:
            return FlashCommand(exit_x, target_y, target_platform=edge["to_platform"])
        return IdleCommand()


# ============================================================
# 自动寻怪策略
# ============================================================

class AutoHuntStrategy(DecisionStrategy):
    """自动寻怪策略：当前平台有怪则打，没怪则找出口切换平台。"""

    def __init__(self, get_skill_cb=None, log_cb=None) -> None:
        super().__init__("auto_hunt", "自动寻怪", get_skill_cb=get_skill_cb, log_cb=log_cb)

    def decide(self, state: GameState, wm: WorldModel,
               patrol_direction: str,
               transition_in_progress: bool,
               min_monsters_on_platform: int,
               patrol_waypoints: list | None,
               current_waypoint_idx: int,
               return_method: str = "一直走") -> tuple[Command | None, str, int, str]:

        cx, cy, log_lines, current_platform, facing = self._init_log(state, wm)
        monsters = state.monsters

        # 感知新鲜度检查
        if not self._check_perception_freshness(state, log_lines):
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        if transition_in_progress:
            log_lines.append("动作: 平台移动中，等待完成...")
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 角色在绳梯上
        if state.on_rope:
            return self._handle_on_rope(state, wm, patrol_direction,
                                         current_waypoint_idx, log_lines)

        on_plat = self._monster_log(log_lines, monsters, cx, cy, facing)
        log_lines.append(f" 阈值={min_monsters_on_platform}")

        # 打怪决策（同平台怪物数 ≥ 阈值）
        if len(on_plat) >= min_monsters_on_platform:
            result = self._hunt_monsters(cx, cy, on_plat, facing,
                                          patrol_direction, current_waypoint_idx, log_lines)
            if result is not None:
                return result
        else:
            log_lines.append(f"动作: 同平台仅{len(on_plat)}只怪物(阈值{min_monsters_on_platform})，切换平台")

        # 寻路决策
        return self._find_exit(state, wm, current_platform,
                               patrol_direction, current_waypoint_idx, log_lines)

    def _hunt_monsters(self, cx: float, cy: float, on_plat: list[dict],
                        facing: str, patrol_direction: str,
                        current_waypoint_idx: int,
                        log_lines: list[str]) -> tuple[Command, str, int, str] | None:
        """打怪决策。返回 None 表示没有可攻击的怪。"""
        nm = nearest_monster(cx, cy, on_plat)
        if nm is None:
            return None

        skill = self._get_skill(len(on_plat))
        skill_key = skill.get("key", "a")
        attack_range = float("inf") if skill.get("fullscreen") else skill.get("range", ATTACK_DISTANCE)

        dx = nm["cx"] - cx
        dy_foot = nm["y2"] - cy
        in_range = abs(dx) < attack_range and abs(dy_foot) < ATTACK_VERTICAL
        same_dir = (dx >= 0) == (facing == 'r')

        front_count = sum(1 for m in on_plat
                          if abs(m["cx"] - cx) < attack_range and abs(m["y2"] - cy) < ATTACK_VERTICAL
                          and ((m["cx"] - cx >= 0) == (facing == 'r')))
        back_count = sum(1 for m in on_plat
                         if abs(m["cx"] - cx) < attack_range and abs(m["y2"] - cy) < ATTACK_VERTICAL
                         and ((m["cx"] - cx >= 0) != (facing == 'r')))
        total_close = front_count + back_count

        if in_range and same_dir:
            log_lines.append(f"动作: 正前方有{total_close}只怪，用{skill.get('name','攻击')}攻击")
            return AttackCommand(skill_key), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
        elif in_range:
            new_facing = 'r' if nm["cx"] > cx else 'l'
            log_lines.append(f"动作: 身后有{total_close}只怪，转身{skill.get('name','攻击')}追击")
            return TurnAndAttackCommand(new_facing, skill_key), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
        else:
            need_jump = dy_foot < JUMP_THRESHOLD
            jmp = " +跳跃" if need_jump else ""
            log_lines.append(f"动作: 追踪最近怪物（距离{((dx)**2+(dy_foot)**2)**0.5:.0f}px）{jmp}")
            return MoveToCommand(nm["cx"], need_jump=need_jump), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    def _find_exit(self, state: GameState, wm: WorldModel,
                    current_platform: str | None,
                    patrol_direction: str,
                    current_waypoint_idx: int,
                    log_lines: list[str]) -> tuple[Command, str, int, str]:
        """平台寻路：找出口切换平台。"""
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
            d_cn = "上" if patrol_direction == "up" else "下"
            log_lines.append(f"动作: 当前平台无怪物，无{d_cn}方出口，待机")
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        edge_type = exit_edge["type"]
        type_cn = TYPE_CN.get(edge_type, "移动")
        d_cn = "上" if patrol_direction == "up" else "下"
        log_lines.append(f"动作: 当前平台无怪物，向{d_cn}巡逻")

        exit_x = wm.get_exit_minimap_x(exit_edge)
        if edge_type == EdgeType.ROPE:
            log_lines.append(f"      到达 小地图x={int(exit_x)} 位置，进行{type_cn}操作")
        else:
            log_lines.append(f"      到达 小地图x={int(exit_x)} 位置，进行{type_cn}操作（→{exit_edge['to_platform']}）")

        cmd = self._create_cmd_from_edge(exit_edge, wm, current_platform, log_cb=self._log)
        return cmd, patrol_direction, current_waypoint_idx, "\n".join(log_lines)


# ============================================================
# 固定路线策略
# ============================================================

class FixedRouteStrategy(DecisionStrategy):
    """固定路线策略：按途经点行进。每帧优先清怪（身前身后都打），没怪再移动。"""

    def __init__(self, get_skill_cb=None, log_cb=None) -> None:
        super().__init__("fixed_route", "固定路线", get_skill_cb=get_skill_cb, log_cb=log_cb)

    def decide(self, state: GameState, wm: WorldModel,
               patrol_direction: str,
               transition_in_progress: bool,
               min_monsters_on_platform: int,
               patrol_waypoints: list | None,
               current_waypoint_idx: int,
               return_method: str = "一直走") -> tuple[Command | None, str, int, str]:

        cx, cy, log_lines, current_platform, facing = self._init_log(state, wm)
        monsters = state.monsters

        if not patrol_waypoints or len(patrol_waypoints) < 2:
            log_lines.append("动作: 无有效巡逻路线，待机")
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 感知新鲜度检查
        if not self._check_perception_freshness(state, log_lines):
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 绳梯上 → 沿绳梯继续爬
        if state.on_rope:
            return self._handle_on_rope(state, wm, patrol_direction,
                                         current_waypoint_idx, log_lines)

        # 确保索引有效
        if current_waypoint_idx >= len(patrol_waypoints):
            current_waypoint_idx = 0
        wp = patrol_waypoints[current_waypoint_idx]
        wp_x, wp_y = wp

        wp_platform = wm.find_platform(wp_x, wp_y)
        plat_label2 = wp_platform or "未知"
        mm_dx = wp_x - state.player_minimap_x
        mm_dy = wp_y - state.player_minimap_y
        log_lines.append(f"途经点[{current_waypoint_idx}]: ({wp_x:.0f},{wp_y:.0f})  "
                         f"平台={plat_label2}  距离=({mm_dx:+.0f},{mm_dy:+.0f})  "
                         f"回归={return_method}")

        # === 优先清怪（身前身后都算）===
        # 数攻击范围内的怪物来决定用单攻还是群攻
        skill = self._get_skill(len([m for m in monsters
            if abs(m["y2"] - cy) <= ATTACK_VERTICAL
            and abs(m["cx"] - cx) < ATTACK_DISTANCE]))
        skill_key = skill.get("key", "a")
        attack_range = float("inf") if skill.get("fullscreen") else skill.get("range", ATTACK_DISTANCE)

        # 同平台攻击范围内的所有怪物
        same_plat_monsters: list[dict] = [m for m in monsters
            if abs(m["y2"] - cy) <= PLATFORM_TOLERANCE
            and abs(m["cx"] - cx) < attack_range
            and abs(m["y2"] - cy) < ATTACK_VERTICAL]

        if same_plat_monsters:
            # 优先打身前
            front: list[dict] = [m for m in same_plat_monsters
                if (m["cx"] - cx >= 0) == (facing == 'r')]
            if front:
                m = front[0]
                info = self._format_monsters(front, cx, cy)
                log_lines.append(f"动作: 身前{info}，用{skill.get('name', '攻击')}攻击")
                return TimedAttackCommand(skill_key), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

            # 身前没有，身后有 → 转身攻击
            behind: list[dict] = [m for m in same_plat_monsters
                if (m["cx"] - cx >= 0) != (facing == 'r')]
            if behind:
                m = behind[0]
                info = self._format_monsters(behind, cx, cy)
                new_dir = 'r' if m["cx"] - cx > 0 else 'l'
                log_lines.append(f"动作: 身后{info}，转身攻击")
                return TurnAndAttackCommand(new_dir, skill_key), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 跨平台移动
        if current_platform and wp_platform and current_platform != wp_platform:
            return self._cross_platform(state, wm, current_platform, wp_platform,
                                         mm_dx, transition_in_progress,
                                         patrol_direction, current_waypoint_idx,
                                         return_method, log_lines)

        # 到达判断：途经点无对应平台时只用水平距离（绳梯/跳跃锚点可能落在平台间隙）
        if wp_platform is None and current_platform:
            arrived = abs(mm_dx) < 10
        else:
            arrived = ((mm_dx)**2 + (mm_dy)**2)**0.5 < 10
        if arrived:
            new_idx = (current_waypoint_idx + 1) % len(patrol_waypoints)
            log_lines.append(f"动作: 到达途经点{current_waypoint_idx}，前往{new_idx}")
            return IdleCommand(), patrol_direction, new_idx, "\n".join(log_lines)

        # 没怪 → 朝途经点方向步行
        move_dir = 'r' if mm_dx > 0 else 'l'
        log_lines.append(f"动作: 同平台向{'右' if move_dir == 'r' else '左'}步行")
        return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

    def _cross_platform(self, state: GameState, wm: WorldModel,
                         current_platform: str, wp_platform: str,
                         mm_dx: float, transition_in_progress: bool,
                         patrol_direction: str, current_waypoint_idx: int,
                         return_method: str,
                         log_lines: list[str]) -> tuple[Command, str, int, str]:
        """跨平台移动逻辑。

        return_method 控制无直接连接时的行为：
        - "无": 通过平台连接图 BFS 寻找路径
        - "一直走": 朝目标方向直线行走
        - "下跳": 按 Alt+↓ 跳下平台
        """
        if transition_in_progress:
            log_lines.append("动作: 跨平台移动中，等待完成...")
            return IdleCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        # 查找直达边
        exit_edge = None
        for e in wm.edges:
            if e.get("from_platform") == current_platform and e.get("to_platform") == wp_platform:
                exit_edge = e
                break

        if exit_edge is None:
            curr_order = wm._platform_order(current_platform)
            wp_order = wm._platform_order(wp_platform)

            if return_method == "一直走":
                # 一直走：朝目标方向步行，不寻路
                move_dir = 'r' if mm_dx > 0 else 'l'
                target_pos = "上方" if wp_order > curr_order else "下方"
                log_lines.append(f"动作: 目标平台在{target_pos}，回归方式=一直走，朝{'右' if move_dir == 'r' else '左'}步行")
                return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

            elif return_method == "下跳":
                # 下跳：Alt+↓ 从平台跳下。但如果目标在上方，下跳白费
                if wp_order > curr_order:
                    # 目标在上方，下跳走错方向，用 BFS 找上方出口
                    exit_edge = wm.find_nearest_exit(current_platform, "up", state.player_minimap_x)
                    if exit_edge is not None:
                        log_lines.append("回归方式=下跳，但目标在上方，改用上方出口")
                    else:
                        move_dir = 'r' if mm_dx > 0 else 'l'
                        log_lines.append(f"回归方式=下跳，目标在上方但无出口，朝{'右' if move_dir == 'r' else '左'}步行")
                        return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
                else:
                    log_lines.append("动作: 回归方式=下跳，Alt+↓ 跳下平台")
                    return JumpDownCommand(), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

            else:  # "无" — 默认：寻找平台连接点计算回归路线
                if wp_order > curr_order:
                    direction = "up"
                else:
                    direction = "down"
                exit_edge = wm.find_nearest_exit(current_platform, direction, state.player_minimap_x)
                if exit_edge is None:
                    move_dir = 'r' if mm_dx > 0 else 'l'
                    log_lines.append(f"动作: 回归方式=无，未找到{direction}方出口，朝{'右' if move_dir == 'r' else '左'}步行")
                    return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)
                else:
                    log_lines.append(f"动作: 回归方式=无，BFS找到{direction}方出口")

        if exit_edge is None:
            move_dir = 'r' if mm_dx > 0 else 'l'
            log_lines.append(f"动作: 无可用出口，朝{'右' if move_dir == 'r' else '左'}方向步行")
            return HoldDirCommand(move_dir), patrol_direction, current_waypoint_idx, "\n".join(log_lines)

        edge_type = exit_edge["type"]
        type_cn = TYPE_CN.get(edge_type, "移动")
        log_lines.append(f"动作: 跨平台 {type_cn} → {wp_platform}")

        cmd = self._create_cmd_from_edge(exit_edge, wm, current_platform, log_cb=self._log)
        return cmd, patrol_direction, current_waypoint_idx, "\n".join(log_lines)


# ============================================================
# 策略注册表
# ============================================================

def create_strategies(get_skill_cb, log_cb=None) -> dict[str, DecisionStrategy]:
    return {
        "auto_hunt": AutoHuntStrategy(get_skill_cb=get_skill_cb, log_cb=log_cb),
        "fixed_route": FixedRouteStrategy(get_skill_cb=get_skill_cb, log_cb=log_cb),
    }


STRATEGIES: dict[str, DecisionStrategy] = {
    "auto_hunt": AutoHuntStrategy(),
    "fixed_route": FixedRouteStrategy(),
}
