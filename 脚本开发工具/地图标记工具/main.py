#!/usr/bin/env python3
"""地图标记工具 — 入口脚本。

用法:
    cd 脚本开发工具/地图标记工具
    python main.py
"""

import ctypes
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path

try:
    from .map_marker_app import MapMarkerApp
except ImportError:
    from map_marker_app import MapMarkerApp

LOG_FILE: Path = Path(__file__).resolve().parent / "marker_output" / "error.log"


def _setup_error_logging() -> None:
    """设置全局异常钩子，将未捕获异常写入 error.log 并弹窗提示。"""
    def _write_log(msg: str) -> None:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass  # 日志写入失败本身不应该导致递归

    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write_log(f"未捕获异常:\n{tb_text}")
        try:
            ctypes.windll.user32.MessageBoxW(
                0, f"发生未处理的错误:\n\n{str(exc_value)}\n\n详情请查看 error.log",
                "地图标记工具 - 错误", 0x10)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    # tkinter 事件循环中的异常也需要捕获
    def _tk_error(exc_type, exc_value, exc_tb):
        import traceback
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write_log(f"Tkinter 事件异常:\n{tb_text}")
        try:
            ctypes.windll.user32.MessageBoxW(
                0, f"操作失败:\n\n{str(exc_value)}\n\n详情请查看 error.log",
                "地图标记工具 - 错误", 0x10)
        except Exception:
            pass

    tk.Tk.report_callback_exception = staticmethod(_tk_error)


def main():
    _setup_error_logging()

    root = tk.Tk()
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"+{(sw - 440) // 2}+{(sh - 520) // 2}")
    MapMarkerApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        err = traceback.format_exc()
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] 启动失败:\n{err}\n")
        except Exception:
            pass
        ctypes.windll.user32.MessageBoxW(0, err, "地图标记 - Error", 0x10)
        raise
