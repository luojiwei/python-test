"""transition.py — 跨平台过渡状态机 + 绳梯卡死监控器。

管理 _loop() 中「正在切换平台 → 是否完成 / 是否应中断」的判断逻辑，
以及绳梯爬升中的卡死检测与自动恢复。
"""

import time
from dataclasses import dataclass


@dataclass
class TransitionResult:
    """过渡状态检查结果。"""
    action: str                     # "continue" | "complete" | "interrupt" | "decide"
    log_message: str = ""


class TransitionController:
    """跨平台过渡状态机。

    核心逻辑：
    - finished: 命令执行完毕 → 重新决策
    - off_rope: 爬梯中离开绳梯范围（可能被撞飞/误触） → 同上
    - near_monster_in_move: 走向绳梯时有近身怪 → 中断，优先清怪
    """

    def __init__(self, actions,  # KeyActionManager
                 log_cb=None,
                 nearby_monster_check=None) -> None:
        self.actions = actions
        self._log = log_cb or (lambda s: None)
        self._nearby_monster = nearby_monster_check or (lambda: False)

        self.in_progress: bool = False
        self._start_time: float = 0.0

    def reset(self) -> None:
        """重置过渡状态（启动时调用）。"""
        self.in_progress = False
        self._start_time = 0.0

    def begin(self, now: float) -> None:
        """标记过渡开始。"""
        self.in_progress = True
        self._start_time = now

    def check(self, command,  # Command | None
              player_y: float, now: float) -> TransitionResult:
        """检查过渡状态，返回当前应执行的动作。

        Args:
            command: 当前命令（用于检查 is_finished / is_on_rope）
            player_y: 角色当前小地图 Y 坐标
            now: 当前时间

        Returns:
            TransitionResult: action 为 "continue" / "complete" / "interrupt"
        """
        if not self.in_progress or command is None:
            return TransitionResult(action="continue")

        from commands import ClimbCommand
        finished = command.is_finished()
        is_climb = isinstance(command, ClimbCommand)
        off_rope = is_climb and command.is_in_climb_state() and not command.is_on_rope(player_y)

        if finished or off_rope:
            self.in_progress = False
            return TransitionResult(action="complete", log_message="到达目标平台，无需重置朝向")

        # turn / move 阶段检测怪物 → 中断，优先清怪
        if is_climb and command.is_in_turn_or_move_stage() and self._nearby_monster():
            self.in_progress = False
            self._log("走向绳梯时发现近身怪物，中断移动优先清怪")
            return TransitionResult(action="interrupt", log_message="近身怪物，中断爬梯移动")

        return TransitionResult(action="continue")


# ============================================================
# 绳梯卡死监控器（原 commands.py 中迁移至此）
# ============================================================

class RopeStuckMonitor:
    """绳梯卡住检测器，ClimbCommand 进入 climb 时激活，x 脱离绳梯时停用。

    只要角色的 x 坐标还在绳梯上，就持续监控位置历史，检测卡住并自动恢复。
    """

    ROPE_X_TOLERANCE: float = 1.0   # x 在此范围内视为"在绳梯上"
    STUCK_FRAMES: int = 10          # 连续不动帧数判定卡住
    RECOVERY_DURATION: float = 0.5  # 恢复模式持续时间

    def __init__(self, log_cb=None) -> None:
        self._log = log_cb or (lambda s: None)
        self._rope_x: float = 0.0
        self._rope_dir: str = "up"     # up / down
        self._active: bool = False
        self._recovering: bool = False
        self._recovery_start: float = 0.0

    def activate(self, rope_x: float, direction: str) -> None:
        """ClimbCommand 进入 climb 时调用，启动监控。"""
        self._rope_x = rope_x
        self._rope_dir = direction
        self._active = True
        self._recovering = False
        self._log(f"[绳梯监控] 已激活 rope_x={rope_x:.0f} dir={direction}")

    def is_active(self) -> bool:
        return self._active

    def tick(self, actions,  # KeyActionManager
             state) -> None:  # GameState
        """每帧调用，检查是否需要恢复操作。"""
        if not self._active:
            return

        px = state.player_minimap_x
        now: float = time.time()

        # 停止条件：x 已脱离绳梯
        if abs(px - self._rope_x) > self.ROPE_X_TOLERANCE:
            self._active = False
            if self._recovering:
                key = 'u' if self._rope_dir == 'up' else 'd'
                actions.release_extra(key)
                self._recovering = False
            self._log(f"[绳梯监控] x 脱离绳梯 (|{px:.0f}-{self._rope_x:.0f}|>1) → 停止监控")
            return

        # 恢复模式：持续追加按爬梯方向（不干扰其他命令的按键）
        if self._recovering:
            key = 'u' if self._rope_dir == 'up' else 'd'
            actions.hold_extra(key)
            if now - self._recovery_start > self.RECOVERY_DURATION:
                actions.release_extra(key)
                self._recovering = False
                self._log("[绳梯监控] 恢复完成")
            return

        # 卡住检测：10 帧位置不变
        history = state.pos_history
        if len(history) >= self.STUCK_FRAMES:
            recent = history[-self.STUCK_FRAMES:]
            py = state.player_minimap_y
            all_same = all(x == px and y == py for x, y in recent)
            if all_same:
                self._recovering = True
                self._recovery_start = now
                key = 'u' if self._rope_dir == 'up' else 'd'
                actions.hold_extra(key)
                self._log(f"[绳梯监控] 检测到卡死! x={px:.0f} y={py:.0f} — 恢复0.5s")
