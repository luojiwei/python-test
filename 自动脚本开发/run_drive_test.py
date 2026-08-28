"""run_drive_test.py — 临时驱动测试：自动启动打怪脚本，监控按键注入与角色移动。

用法: python run_drive_test.py [秒数]
会自动控制游戏角色，跑完自动停止。
"""

import ctypes
import sys
import time
import tkinter as tk
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import main as m

RUN_SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
LOG_FILE = SCRIPT_DIR / "run.log"

VK_LEFT, VK_RIGHT = 0x25, 0x27


def key_state(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def main() -> None:
    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口，仍能运行主循环线程
    app = m.AutoFarmV2App(root)

    print(f"[驱动] 2 秒后自动开始打怪，运行 {RUN_SECONDS}s ...")
    root.after(2000, app.start)
    root.after(int(2000 + RUN_SECONDS * 1000), app.stop)
    root.after(int(2500 + RUN_SECONDS * 1000), root.quit)

    # 监控线程：每 2 秒采样一次按键注入状态 + 游戏位置
    last_x = None

    def monitor() -> None:
        nonlocal last_x
        if not app.running:
            root.after(2000, monitor)
            return
        l_down = key_state(VK_LEFT)
        r_down = key_state(VK_RIGHT)
        x = app.state.player_minimap_x
        y = app.state.player_minimap_y
        held = list(getattr(app.actions.keys, "_held", set())) if app.actions else []
        cmd = type(app._current_command).__name__ if app._current_command else "None"
        fg = ctypes.windll.user32.GetForegroundWindow()
        fg_ok = (fg == app.target_hwnd)
        if last_x is None or abs(x - last_x) >= 2 or True:
            move = f"  dx={x - last_x:+.0f}" if last_x is not None else ""
            print(f"[监控] L={int(l_down)} R={int(r_down)} held={held} "
                  f"cmd={cmd} fg={'OK' if fg_ok else 'LOST!'} "
                  f"pos=({x:.1f},{y:.1f}){move}")
        last_x = x
        root.after(2000, monitor)

    root.after(2500, monitor)
    root.mainloop()

    print("[驱动] 结束。决策日志见 run.log:")
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    for line in lines[-25:]:
        print("  ", line)


if __name__ == "__main__":
    main()
