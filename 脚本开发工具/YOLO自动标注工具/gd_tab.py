"""gd_tab.py — Tab 2: YOLO 多模型粗标 + 审核 (PySide6)"""

import shutil
import os
import sys
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                                QLabel, QGroupBox, QPlainTextEdit, QMessageBox,
                                QComboBox)
from PySide6.QtCore import Qt, Signal, QObject

from config import (SCREENSHOTS_DIR, LABELS_TRAIN_DIR, LABELS_VAL_DIR,
                    IMAGES_TRAIN_DIR, IMAGES_VAL_DIR, SCRIPTS_DIR, YOLO_PYTHON,
                    load_reviewed_stems, save_reviewed_stems, save_review_round,
                    get_available_models)
from review_dialog import ReviewDialog


class GDTab(QObject):
    """Tab 2 — 自动标注审核：多模型标注 + 弹窗审核 + 导出"""

    log_signal = Signal(str)
    status_signal = Signal(str)
    refresh_signal = Signal()
    review_signal = Signal(list)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app

    def build(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # 说明
        info = QGroupBox("阶段说明")
        info_layout = QVBoxLayout(info)
        info_layout.addWidget(QLabel(
            "使用 trained_models/ 下所有 YOLO 模型合并标注，统一 class_id 输出。\n"
            "标注完成后弹窗逐一审核。累计审核 500 张后建议切换到 Tab 3 自举加速。"))
        layout.addWidget(info)

        # 操作按钮
        row2 = QHBoxLayout()
        btn_label = QPushButton("自动标注")
        btn_label.clicked.connect(self._run_gd_label)
        row2.addWidget(btn_label)
        btn_review = QPushButton("审核上一批")
        btn_review.clicked.connect(self._review_last_batch)
        row2.addWidget(btn_review)

        row2.addStretch()
        row2.addWidget(QLabel("模型:"))
        self._gd_model_combo = QComboBox()
        self._gd_model_combo.addItem("(自动最新)", "")
        for model_path in get_available_models():
            self._gd_model_combo.addItem(model_path.name, str(model_path))
        self._gd_model_combo.setToolTip("选择标注用的模型文件，空=自动选最新")
        row2.addWidget(self._gd_model_combo)
        self._gd_status_var = QLabel("就绪")
        row2.addWidget(self._gd_status_var)
        row2.addStretch()
        layout.addLayout(row2)

        # 输出日志
        self._gd_log = QPlainTextEdit()
        self._gd_log.setReadOnly(True)
        self._gd_log.setMaximumBlockCount(2000)
        layout.addWidget(self._gd_log)

        # 跨线程信号连接
        self.log_signal.connect(self._log_gd)
        self.status_signal.connect(self._gd_status_var.setText)
        self.refresh_signal.connect(self.app._refresh_pool_stats)
        self.review_signal.connect(self._review_batch)

    # ============================================================
    # GD 标注
    # ============================================================
    def _run_gd_label(self) -> None:
        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        if not images:
            QMessageBox.information(self.app, "提示", "图片池为空，请先截图")
            return

        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)
        labeled_stems = ({f.stem for f in LABELS_TRAIN_DIR.glob("*.txt")}
                         | {f.stem for f in LABELS_VAL_DIR.glob("*.txt")})
        batch = [img for img in images if img.stem not in labeled_stems]

        if not batch:
            QMessageBox.information(self.app, "提示", "所有图片已标注")
            self._log_gd("所有图片已标注完毕")
            return

        self._gd_status_var.setText(f"标注中... ({len(batch)} 张)")
        self._log_gd(f"开始多模型标注: {len(batch)} 张")

        label_script = SCRIPTS_DIR / "yolo_labeler.py"
        if not label_script.exists():
            self._log_gd("错误: scripts/yolo_labeler.py 不存在")
            self._gd_status_var.setText("标注脚本缺失")
            return

        cmd = [
            str(YOLO_PYTHON), str(label_script),
            str(out_dir), str(LABELS_TRAIN_DIR),
            "--device", "auto",
        ]
        model_path = self._gd_model_combo.currentData()
        if model_path:
            cmd += ["--model-file", model_path]
            self._log_gd(f"指定模型: {Path(model_path).name}")

        def run():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace",
                                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                for line in proc.stdout:
                    self.log_signal.emit(line.rstrip())
                proc.wait()
                self.log_signal.emit(f"GD 标注完成: {len(batch)} 张")
                self.status_signal.emit(f"完成 {len(batch)} 张，可开始审核")
                self.refresh_signal.emit()
                self.review_signal.emit(batch)
            except FileNotFoundError:
                self.log_signal.emit(f"错误: Python 环境不可用 {YOLO_PYTHON}")
                self.status_signal.emit("环境错误")
            except Exception as e:
                self.log_signal.emit(f"标注异常: {e}")
                self.status_signal.emit("标注失败")

        threading.Thread(target=run, daemon=True).start()

    def _review_last_batch(self) -> None:
        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")],
                        key=lambda f: f.stat().st_mtime)  # 按截图时间排序
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)
        # 同时检查 train 和 val 目录的标注
        labeled = [img for img in images
                   if (LABELS_TRAIN_DIR / f"{img.stem}.txt").exists()
                   or (LABELS_VAL_DIR / f"{img.stem}.txt").exists()]
        # 如果 val 标注对应的截图不在截图池中，从 images/val/ 复制过来
        val_stems = {f.stem for f in LABELS_VAL_DIR.glob("*.txt")}
        img_stems = {img.stem for img in images}
        import shutil
        for missing_stem in val_stems - img_stems:
            src = IMAGES_VAL_DIR / f"{missing_stem}.png"
            if src.exists():
                dst = out_dir / f"{missing_stem}.png"
                shutil.copy2(str(src), str(dst))
        if val_stems - img_stems:
            # 重新加载图片列表
            images = sorted([f for f in out_dir.iterdir()
                             if f.suffix.lower() in (".png", ".jpg", ".jpeg")],
                            key=lambda f: f.stat().st_mtime)
            labeled = [img for img in images
                       if (LABELS_TRAIN_DIR / f"{img.stem}.txt").exists()
                       or (LABELS_VAL_DIR / f"{img.stem}.txt").exists()]
        batch = labeled  # 全部，不限制数量
        if not batch:
            QMessageBox.information(self.app, "提示", "没有可审核的批次")
            return
        self._review_batch(batch)

    def _review_batch(self, images: list[Path]) -> None:
        # 传入全部图片 + 已审查集合，弹窗内部分屏显示
        reviewed = load_reviewed_stems()
        unreviewed_count = len([img for img in images if img.stem not in reviewed])
        if unreviewed_count == 0 and not images:
            QMessageBox.information(self.app, "提示", "没有待审查的新图片")
            return
        elif unreviewed_count == 0:
            self._log_gd(f"全部 {len(images)} 张已审查，可切换「已审查」查看")

        def on_reviewed(stems: set[str]):
            if stems:
                save_reviewed_stems(stems)
                round_num = save_review_round(stems)
                self._log_gd(f"已审查: {len(stems)} 张 → 轮次 {round_num}, 缓存已更新")
            self.app._refresh_pool_stats()

        dialog = ReviewDialog(self.app, images, LABELS_TRAIN_DIR, LABELS_VAL_DIR,
                              on_close=on_reviewed, reviewed_stems=reviewed)
        dialog.exec()

    def _log_gd(self, msg: str) -> None:
        ts = datetime.now().strftime("[%H:%M:%S]")
        self._gd_log.appendPlainText(f"{ts} {msg}")

    def _get_screenshot_dir(self) -> Path:
        return Path(self.app._get_screenshot_dir())

    def _ui_invoke(self, fn):
        """从子线程安全调用 UI。"""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, fn)
