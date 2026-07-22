#!/usr/bin/env python3
"""地图标记工具 — 入口脚本。

用法:
    cd 脚本开发工具/地图标记工具
    python main.py
"""

import ctypes
import tkinter as tk

try:
    from .map_marker_app import MapMarkerApp
except ImportError:
    from map_marker_app import MapMarkerApp


def main():
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
        ctypes.windll.user32.MessageBoxW(0, err, "地图标记 - Error", 0x10)
        raise
