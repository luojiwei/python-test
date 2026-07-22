"""transition.py — 跨平台过渡状态机。

管理 _loop() 中「正在切换平台 → 是否完成 / 是否应中断」的判断逻辑，
消除 commands.py decide() 和 main.py _loop() 之间的状态理解分裂。
"""

import random
from dataclasses import dataclass


@dataclass
class TransitionResult:
    """过渡状态检查结果。"""
    action: str                     # "continue" | "complete" | "interrupt" | "decide"
    log_message: str = ""


class TransitionController:
    """跨平台过渡状态机。

    核心逻辑：
    - finished: 命令执行完毕 → 重置朝向，重新决策
    - off_rope: 爬梯中离开绳梯范围（可能被吹飞/误触） → 同上
    - near_monster_on_rope: 爬梯中有近身怪 → 中断爬梯优先清怪
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
        off_rope = is_climb and not command.is_on_rope(player_y)

        if finished or off_rope:
            self.in_progress = False
            self.actions.turn(random.choice(('l', 'r')))
            self._log("到达目标平台，重置朝向↗，重新决策")
            return TransitionResult(action="complete", log_message="到达目标平台，重置朝向")

        if is_climb and not command.is_on_rope(player_y) and self._nearby_monster():
            self.in_progress = False
            self._log("发现近身怪物，中断上梯优先清怪")
            return TransitionResult(action="interrupt", log_message="近身怪物，中断爬梯")

        return TransitionResult(action="continue")
