"""screenshot_tab.py — Tab 1: 截图采集 (PySide6)"""

import shutil
import threading
import time
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                                QLabel, QLineEdit, QGroupBox, QListWidget,
                                QDialog, QDialogButtonBox, QSizePolicy)
from PySide6.QtCore import Qt, Signal, QObject

import config
from config import (SCREENSHOTS_DIR, WINDOW_TITLE, TARGET_W, TARGET_H, INTERVAL,
                    IMAGES_TRAIN_DIR, IMAGES_VAL_DIR, LABELS_TRAIN_DIR,
                    LABELS_VAL_DIR, ensure_screenshot_libs)
from utils import (enum_visible_windows, find_window_by_title, force_foreground,
                   capture_and_save)


class ScreenshotTab(QObject):
    """Tab 1 — 截图采集"""

    status_signal = Signal(str)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app

    def build(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # 窗口选择
        row = QHBoxLayout()
        row.addWidget(QLabel("目标窗口:"))
        self._window_var = QLineEdit(WINDOW_TITLE)
        self._window_var.setMinimumWidth(180)
        row.addWidget(self._window_var)
        btn_pick = QPushButton("选择窗口")
        btn_pick.clicked.connect(self._pick_window)
        row.addWidget(btn_pick)
        btn_browse = QPushButton("浏览窗口")
        btn_browse.clicked.connect(self._browse_windows)
        row.addWidget(btn_browse)
        row.addStretch()
        layout.addLayout(row)

        # 输出目录（只展示，不编辑）
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("截图目录:"))
        dir_label = QLabel(str(SCREENSHOTS_DIR))
        dir_label.setStyleSheet("color: #AAA; font-family: monospace;")
        row2.addWidget(dir_label)
        row2.addStretch()
        btn_open = QPushButton("打开目录")
        import subprocess, os
        btn_open.clicked.connect(lambda: subprocess.Popen(
            ["explorer", str(SCREENSHOTS_DIR)] if os.name == "nt" else ["open", str(SCREENSHOTS_DIR)]))
        row2.addWidget(btn_open)
        layout.addLayout(row2)

        # 地图名称 + 截图频率
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("地图名称:"))
        self._map_name_var = QLineEdit("")
        self._map_name_var.setMaximumWidth(140)
        row3.addWidget(self._map_name_var)
        row3.addWidget(QLabel("  截图间隔(秒):"))
        self._interval_var = QLineEdit(str(INTERVAL))
        self._interval_var.setMaximumWidth(60)
        row3.addWidget(self._interval_var)
        row3.addStretch()
        layout.addLayout(row3)

        # 控制按钮
        row4 = QHBoxLayout()
        self._screenshot_btn = QPushButton("开始截图")
        self._screenshot_btn.clicked.connect(self._toggle_screenshot)
        row4.addWidget(self._screenshot_btn)
        self._screenshot_status = QLabel("就绪")
        row4.addWidget(self._screenshot_status)

        # 跨线程信号连接
        self.status_signal.connect(self._screenshot_status.setText)
        row4.addStretch()
        layout.addLayout(row4)

        # 图片池统计
        stat_frame = QGroupBox("图片池统计")
        stat_layout = QVBoxLayout(stat_frame)
        self._pool_stats = QLabel("图片池：0 张  |  已标注：0 张  |  待标：0 张")
        stat_layout.addWidget(self._pool_stats)
        layout.addWidget(stat_frame)

        btn_refresh = QPushButton("刷新统计")
        btn_refresh.clicked.connect(self.refresh_pool_stats)
        layout.addWidget(btn_refresh)

        layout.addStretch()

    # ============================================================
    # 窗口选择
    # ============================================================
    def _pick_window(self) -> None:
        title = self._window_var.text().strip()
        if not title:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self.app, "提示", "请先输入窗口标题关键词")
            return
        windows = find_window_by_title(title)
        if not windows:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self.app, "提示", f"未找到包含 '{title}' 的窗口")
            return
        if len(windows) == 1:
            self.app._target_hwnd = windows[0][0]
            self._window_var.setText(windows[0][1])
            return
        self._show_window_picker(windows)

    def _browse_windows(self) -> None:
        windows = enum_visible_windows(200, 200)
        if not windows:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self.app, "提示", "未找到可用窗口")
            return
        self._show_window_browser(windows)

    def _show_window_picker(self, windows: list) -> None:
        dlg = QDialog(self.app)
        dlg.setWindowTitle("选择窗口")
        dlg.resize(600, 280)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"找到 {len(windows)} 个窗口，请选择:"))

        lb = QListWidget()
        for i, (_, wt, l, t, r, b, pid) in enumerate(windows):
            lb.addItem(f"  [{i}] {wt[:50]:<52}  {r-l}x{b-t:<10}  PID={pid}")
        lb.setCurrentRow(0)
        layout.addWidget(lb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.Accepted:
            sel = lb.currentRow()
            if sel >= 0:
                self.app._target_hwnd = windows[sel][0]
                self._window_var.setText(windows[sel][1])

    def _show_window_browser(self, windows: list) -> None:
        dlg = QDialog(self.app)
        dlg.setWindowTitle("浏览窗口")
        dlg.resize(700, 350)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"共 {len(windows)} 个窗口:"))

        lb = QListWidget()
        for i, (_, wt, l, t, r, b, pid) in enumerate(windows):
            wt_short = wt[:55] + ("..." if len(wt) > 55 else "")
            lb.addItem(f"  [{i:02d}] {wt_short:<58}  {r-l}x{b-t:<10}  PID={pid}")
        layout.addWidget(lb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.Accepted:
            sel = lb.currentRow()
            if sel >= 0:
                self.app._target_hwnd = windows[sel][0]
                self._window_var.setText(windows[sel][1])

    def _browse_screenshot_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self.app, "选择截图保存目录")
        if d:
            self._screenshot_dir_var.setText(d)

    # ============================================================
    # 截图控制
    # ============================================================
    def _toggle_screenshot(self) -> None:
        if self.app._screenshot_running:
            self._stop_screenshot()
        else:
            self._start_screenshot()

    def _start_screenshot(self) -> None:
        if not ensure_screenshot_libs():
            return

        title = self._window_var.text().strip()
        if not title:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self.app, "提示", "请先输入/选择目标窗口")
            return
        windows = find_window_by_title(title)
        if not windows:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self.app, "提示", f"未找到包含 '{title}' 的窗口")
            return
        self.app._target_hwnd = windows[0][0]

        out_dir = Path(str(SCREENSHOTS_DIR))
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            force_foreground(self.app._target_hwnd)
        except Exception:
            pass
        time.sleep(0.3)

        self.app._screenshot_running = True
        self.app._processed_count = 0
        self._screenshot_btn.setText("停止截图")
        self._screenshot_status.setText("运行中...")

        map_name = self._map_name_var.text().strip()

        # 缓存间隔值到子线程安全变量（Qt widget 不能在子线程读取）
        try:
            interval = float(self._interval_var.text())
        except ValueError:
            interval = 1.0

        self.app._screenshot_thread = threading.Thread(
            target=self._screenshot_loop, args=(out_dir, map_name, interval), daemon=True
        )
        self.app._screenshot_thread.start()

    def _stop_screenshot(self) -> None:
        self.app._screenshot_running = False
        self._screenshot_btn.setText("开始截图")
        self._screenshot_status.setText(f"已停止  共截 {self.app._processed_count} 张")

    def _screenshot_loop(self, out_dir: Path, map_name: str, interval: float) -> None:
        with config.mss.mss() as sct:
            last_status = time.time()
            while self.app._screenshot_running:
                t0 = time.time()
                try:
                    fname = capture_and_save(sct, self.app._target_hwnd, out_dir, map_name)
                    if fname:
                        self.app._processed_count += 1
                except Exception as e:
                    import traceback
                    print(f"[截图异常] {e}", flush=True)
                    traceback.print_exc()
                    self.status_signal.emit(f"异常: {e}")
                    break

                if time.time() - last_status > 0.5:
                    self.status_signal.emit(
                        f"运行中... {self.app._processed_count} 张")
                    last_status = time.time()

                sleep_t = interval - (time.time() - t0)
                if sleep_t > 0:
                    time.sleep(sleep_t)

    # ============================================================
    # 统计
    # ============================================================
    def refresh_pool_stats(self) -> None:
        out_dir = Path(str(SCREENSHOTS_DIR))
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)
        total = len(images)
        labeled = (len(list(LABELS_TRAIN_DIR.glob("*.txt"))) +
                   len(list(LABELS_VAL_DIR.glob("*.txt"))))
        self._pool_stats.setText(
            f"图片池：{total} 张  |  已标注：{labeled} 张  |  待标：{max(0, total - labeled)} 张")
