"""auto_labeler_gui.py — YOLO 自动标注工具入口

工作流：
  Tab 1 — 截图采集：窗口截图，存入图片池
  Tab 2 — GD 冷启动：Grounding DINO 粗标 + 弹窗审核
  Tab 3 — YOLO 自举：审核 500 张后训 YOLO，逐轮减少人工
"""

import json
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from config import CONFIG_FILE, SCREENSHOTS_DIR
from screenshot_tab import ScreenshotTab
from gd_tab import GDTab
from yolo_tab import YOLOTab


class AutoLabelerGUI:
    """三 Tab 自动标注工具主窗口。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("YOLO 自动标注工具")
        root.geometry("640x720")
        root.minsize(640, 720)

        # ---- 共享状态 ----
        self._screenshot_running: bool = False
        self._target_hwnd: int | None = None
        self._screenshot_thread: threading.Thread | None = None
        self._processed_count: int = 0
        self._gd_batch_size: int = 100
        self._yolo_round: int = 0

        # ---- 配置持久化 ----
        self._config: dict = {}
        self._load_config()

        # ---- UI 构建 ----
        self._build_notebook()
        self._restore_config()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ============================================================
    # 配置 ~/.config_cache.json
    # ============================================================
    def _load_config(self) -> None:
        if CONFIG_FILE.exists():
            try:
                self._config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._config = {}
        else:
            self._config = {}

    def _save_config(self) -> None:
        CONFIG_FILE.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _restore_config(self) -> None:
        pass

    def _on_close(self) -> None:
        self._save_config()
        self.root.destroy()

    def _get_config(self, key: str, default=None):
        return self._config.get(key, default)

    def _set_config(self, key: str, value) -> None:
        self._config[key] = value

    def _get_screenshot_dir(self) -> str:
        """HACK: 从 ScreenshotTab 的 StringVar 取截图目录。
        各 Tab 通过 app._get_screenshot_dir() 统一访问。"""
        try:
            return self._screenshot_tab._screenshot_dir_var.get()
        except Exception:
            return str(SCREENSHOTS_DIR)

    # ============================================================
    # Notebook 组装
    # ============================================================
    def _build_notebook(self) -> None:
        self._notebook = ttk.Notebook(self.root)
        self._notebook.pack(fill="both", expand=True)

        # Tab 1 — 截图采集
        tab1 = ttk.Frame(self._notebook)
        self._screenshot_tab = ScreenshotTab(self)
        self._screenshot_tab.build(tab1)
        self._notebook.add(tab1, text="截图采集")

        # Tab 2 — GD 粗标审核
        tab2 = ttk.Frame(self._notebook)
        self._gd_tab = GDTab(self)
        self._gd_tab.build(tab2)
        self._notebook.add(tab2, text="自动标注审核")

        # Tab 3 — YOLO 自举训练
        tab3 = ttk.Frame(self._notebook)
        self._yolo_tab = YOLOTab(self)
        self._yolo_tab.build(tab3)
        self._notebook.add(tab3, text="YOLO 自举训练")

    # ============================================================
    # 跨 Tab 公用方法
    # ============================================================
    def _refresh_pool_stats(self) -> None:
        self._screenshot_tab.refresh_pool_stats()


def main() -> None:
    root = tk.Tk()
    AutoLabelerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
