"""auto_labeler_gui.py — YOLO 自动标注工具入口 (PySide6)

工作流：
  Tab 1 — 截图采集：窗口截图，存入图片池
  Tab 2 — GD 冷启动：YOLO 多模型粗标 + 弹窗审核
  Tab 3 — YOLO 自举：审核 500 张后训 YOLO，逐轮减少人工
"""

import json
import sys
import threading
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget,
                                QVBoxLayout)
from PySide6.QtCore import Qt

from config import CONFIG_FILE, SCREENSHOTS_DIR
from screenshot_tab import ScreenshotTab
from gd_tab import GDTab
from yolo_tab import YOLOTab


class AutoLabelerGUI(QMainWindow):
    """三 Tab 自动标注工具主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YOLO 自动标注工具")
        self.resize(640, 720)
        self.setMinimumSize(640, 720)

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
        self._build_ui()

        # ---- 恢复配置 ----
        self._restore_config()

    # ============================================================
    # 配置 ~/config_cache.json
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

    def _get_config(self, key: str, default=None):
        return self._config.get(key, default)

    def _set_config(self, key: str, value) -> None:
        self._config[key] = value

    def _get_screenshot_dir(self) -> str:
        return str(SCREENSHOTS_DIR)

    # ============================================================
    # UI 组装
    # ============================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self._notebook = QTabWidget()
        layout.addWidget(self._notebook)

        # Tab 1 — 截图采集
        tab1 = QWidget()
        self._screenshot_tab = ScreenshotTab(self)
        self._screenshot_tab.build(tab1)
        self._notebook.addTab(tab1, "截图采集")

        # Tab 2 — 自动标注审核
        tab2 = QWidget()
        self._gd_tab = GDTab(self)
        self._gd_tab.build(tab2)
        self._notebook.addTab(tab2, "自动标注审核")

        # Tab 3 — YOLO 自举训练
        tab3 = QWidget()
        self._yolo_tab = YOLOTab(self)
        self._yolo_tab.build(tab3)
        self._notebook.addTab(tab3, "YOLO 自举训练")

    # ============================================================
    # 跨 Tab 公用方法
    # ============================================================
    def _refresh_pool_stats(self) -> None:
        self._screenshot_tab.refresh_pool_stats()

    def closeEvent(self, event):
        self._save_config()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # 统一风格
    window = AutoLabelerGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
