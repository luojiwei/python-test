"""yolo_tab.py — Tab 3: YOLO 自举训练 + 自动标注 (PySide6)"""

import shutil
import os
import sys
import subprocess
import threading
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                                QLabel, QLineEdit, QComboBox, QGroupBox,
                                QPlainTextEdit, QMessageBox,
                                QTextEdit)
from PySide6.QtCore import Qt, Signal, QObject

from config import (PROJECT_DIR, SCREENSHOTS_DIR, DATASET_DIR, IMAGES_TRAIN_DIR,
                    IMAGES_VAL_DIR, LABELS_TRAIN_DIR, LABELS_VAL_DIR, DATA_YAML,
                    OUTPUTS_DIR, MODELS_DIR, SCRIPTS_DIR, YOLO_PYTHON, TARGET_W, TARGET_H,
                    load_reviewed_stems, save_reviewed_stems, save_review_round,
                    get_available_models)
from review_dialog import ReviewDialog
from gpu_utils import detect_gpu, get_device_list, resolve_device, get_gpu_status_text

METRICS_FILE = MODELS_DIR / "metrics.json"

def _save_metrics(pt_path: Path, map50_line: str, precision_line: str,
                  recall_line: str, train_count: int, epochs: str) -> None:
    """保存模型验证指标。"""
    import re, json
    version = pt_path.stem
    map50, p, r = 0.0, 0.0, 0.0
    for line, target in [(map50_line, "map50"), (precision_line, "p"), (recall_line, "r")]:
        m = re.search(r"[\d.]+", line)
        if m:
            if target == "map50": map50 = float(m.group())
            elif target == "p": p = float(m.group())
            elif target == "r": r = float(m.group())
    entry = {
        "version": version,
        "map50": map50,
        "precision": p,
        "recall": r,
        "images": train_count,
        "epochs": int(epochs),
    }
    all_metrics = {}
    if METRICS_FILE.exists():
        all_metrics = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    all_metrics[version] = entry
    METRICS_FILE.write_text(json.dumps(all_metrics, ensure_ascii=False, indent=2), encoding="utf-8")


class YOLOTab(QObject):
    """Tab 3 — YOLO 自举训练: 训练 YOLO → 自动标注 → 低置信审核 → 重训循环"""

    # 跨线程信号
    log_signal = Signal(str)
    status_signal = Signal(str)
    refresh_signal = Signal()
    review_signal = Signal(list)

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._device_values: list[str] = []

    def build(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # 说明
        info = QGroupBox("阶段说明")
        info_layout = QVBoxLayout(info)
        info_layout.addWidget(QLabel(
            "用已审核的标注训练 YOLO，然后用 YOLO 自动标注新图片。\n"
            "高置信度(>0.8)自动通过，低置信度弹窗审核。每 500 张重训一轮。"))
        layout.addWidget(info)

        # GPU 状态显示 + 设备选择
        gpu_frame = QHBoxLayout()
        try:
            gpu_text = get_gpu_status_text()
        except Exception:
            gpu_text = "GPU: 检测失败"
        self._gpu_label_var = QLabel(f"[GPU] {gpu_text}")
        self._gpu_label_var.setStyleSheet("color: #2E7D32;")
        gpu_frame.addWidget(self._gpu_label_var)
        gpu_frame.addWidget(QLabel("  设备:"))
        self._device_combo = QComboBox()
        devices = get_device_list()
        self._device_values = [v for v, t in devices]
        for v, t in devices:
            self._device_combo.addItem(t, v)
        self._device_combo.currentIndexChanged.connect(self._save_device_config)
        gpu_frame.addWidget(self._device_combo)
        gpu_frame.addStretch()
        layout.addLayout(gpu_frame)

        # 训练参数
        train_params = QGroupBox("训练参数")
        train_grid = QVBoxLayout(train_params)

        row0 = QHBoxLayout()
        lbl = QLabel("预训练模型:")
        lbl.setToolTip("从哪个模型开始训练\nyolo11n.pt = 官方 nano 模型（推荐）\n改成自己的 v1.pt 会从上一版微调")
        row0.addWidget(lbl)
        self._yolo_model_var = QLineEdit("yolo11n.pt")
        self._yolo_model_var.setMaximumWidth(130)
        row0.addWidget(self._yolo_model_var)

        lbl = QLabel("轮数(epochs):")
        lbl.setToolTip("训练多少轮\n50=快速验证, 100=标准训练, 200+=精细训练\n数据越多需要越少轮数")
        row0.addWidget(lbl)
        self._yolo_epochs_var = QLineEdit("50")
        self._yolo_epochs_var.setMaximumWidth(50)
        row0.addWidget(self._yolo_epochs_var)

        lbl = QLabel("批次(batch):")
        lbl.setToolTip("每次训练几张图\nCPU 建议 4-8, GPU 可 16-32\n太大=显存不够, 太小=训练不稳定")
        row0.addWidget(lbl)
        self._yolo_batch_var = QLineEdit("16")
        self._yolo_batch_var.setMaximumWidth(50)
        row0.addWidget(self._yolo_batch_var)

        lbl = QLabel("图片尺寸(imgsz):")
        lbl.setToolTip("输入图片缩放到多大\n640=标准, 1280=更精细但更慢\n像素游戏推荐 640 就够")
        row0.addWidget(lbl)
        self._yolo_imgsz_var = QLineEdit("640")
        self._yolo_imgsz_var.setMaximumWidth(50)
        row0.addWidget(self._yolo_imgsz_var)
        row0.addStretch()
        train_grid.addLayout(row0)

        # 高级参数
        row_adv = QHBoxLayout()
        lbl = QLabel("学习率(lr):")
        lbl.setToolTip("初始学习率\n0.01=标准, 调小(0.001)收敛更稳但慢\n数据少时建议调小")
        row_adv.addWidget(lbl)
        self._yolo_lr_var = QLineEdit("0.01")
        self._yolo_lr_var.setMaximumWidth(50)
        row_adv.addWidget(self._yolo_lr_var)

        lbl = QLabel("Mosaic增强:")
        lbl.setToolTip("把4张图拼成1张训练\n1.0=全开(推荐), 0=关闭\n开=模型更泛化, 关=对特定场景更准")
        row_adv.addWidget(lbl)
        self._yolo_mosaic_var = QLineEdit("1.0")
        self._yolo_mosaic_var.setMaximumWidth(45)
        row_adv.addWidget(self._yolo_mosaic_var)

        lbl = QLabel("早停(patience):")
        lbl.setToolTip("连续多少轮不涨就停止\n20=默认, 调大=不怕慢, 调小=快速止损")
        row_adv.addWidget(lbl)
        self._yolo_patience_var = QLineEdit("20")
        self._yolo_patience_var.setMaximumWidth(40)
        row_adv.addWidget(self._yolo_patience_var)
        row_adv.addStretch()
        train_grid.addLayout(row_adv)

        layout.addWidget(train_params)

        # 参数说明
        param_help = QLabel(
            "参数说明 | 预训练模型: 训练起点(yolo11n.pt=官方) | "
            "轮数: 50快验/100标准 | 批次: GPU可16-32,CPU减半 | "
            "图片尺寸: 640够用 | 学习率: 数据少调小(0.001) | "
            "Mosaic: 1.0=4图拼接增强泛化 | 早停: N轮不涨自动停止"
        )
        param_help.setStyleSheet("color: #888; font-size: 11px;")
        param_help.setWordWrap(True)
        layout.addWidget(param_help)

        # 自举参数
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("高置信直通阈值:"))
        self._yolo_auto_var = QLineEdit("0.8")
        self._yolo_auto_var.setMaximumWidth(50)
        row1.addWidget(self._yolo_auto_var)
        row1.addWidget(QLabel("  低置信审查阈值:"))
        self._yolo_review_var = QLineEdit("0.3")
        self._yolo_review_var.setMaximumWidth(50)
        row1.addWidget(self._yolo_review_var)
        row1.addWidget(QLabel("  每轮重训张数:"))
        self._yolo_retrain_var = QLineEdit("500")
        self._yolo_retrain_var.setMaximumWidth(50)
        row1.addWidget(self._yolo_retrain_var)
        row1.addStretch()
        layout.addLayout(row1)

        # 类别名称
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("类别名(每行一个):"))
        self._yolo_classes_text = QTextEdit()
        self._yolo_classes_text.setMaximumHeight(80)
        self._yolo_classes_text.setPlainText("怪物\n绳子上\n绳子下\n梯子上\n梯子下")
        row2.addWidget(self._yolo_classes_text)
        layout.addLayout(row2)

        # 操作按钮
        row3 = QHBoxLayout()
        btn_train = QPushButton("训练 YOLO")
        btn_train.clicked.connect(self._run_yolo_train)
        row3.addWidget(btn_train)
        btn_label = QPushButton("YOLO 自动标注")
        btn_label.clicked.connect(self._run_yolo_label)
        row3.addWidget(btn_label)
        row3.addWidget(QLabel("标注模型:"))
        self._yolo_label_model_combo = QComboBox()
        self._yolo_label_model_combo.addItem("(最新训练)", "")
        for model_path in get_available_models():
            self._yolo_label_model_combo.addItem(model_path.name, str(model_path))
        self._yolo_label_model_combo.setToolTip("选择标注用的模型，空=使用训练输出的 best.pt")
        row3.addWidget(self._yolo_label_model_combo)
        btn_stats = QPushButton("训练统计")
        btn_stats.clicked.connect(self._show_stats)
        row3.addWidget(btn_stats)
        self._yolo_status_var = QLabel("就绪")
        row3.addWidget(self._yolo_status_var)
        row3.addStretch()
        layout.addLayout(row3)

        # 输出日志
        self._yolo_log = QPlainTextEdit()
        self._yolo_log.setReadOnly(True)
        self._yolo_log.setMaximumBlockCount(2000)
        layout.addWidget(self._yolo_log)

        # 恢复配置
        self._restore_device_config()

        # 跨线程信号连接
        self.log_signal.connect(self._log_yolo)
        self.status_signal.connect(self._yolo_status_var.setText)
        self.refresh_signal.connect(self.app._refresh_pool_stats)
        self.review_signal.connect(self._review_yolo_batch)

    # ============================================================
    # 配置持久化
    # ============================================================
    def _save_device_config(self) -> None:
        idx = self._device_combo.currentIndex()
        if 0 <= idx < len(self._device_values):
            self.app._set_config("yolo_device", self._device_values[idx])

    def _restore_device_config(self) -> None:
        cached = self.app._get_config("yolo_device", "auto")
        if cached in self._device_values:
            idx = self._device_values.index(cached)
            self._device_combo.setCurrentIndex(idx)

    # ============================================================
    # 设备解析
    # ============================================================
    def _get_selected_device(self) -> str:
        return self._device_combo.currentData()

    # ============================================================
    # data.yaml 生成
    # ============================================================
    def generate_data_yaml(self) -> Path:
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_VAL_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)

        # 只同步已审查过的图片，80/20 划分 train/val
        reviewed = load_reviewed_stems()
        if reviewed:
            # 清空旧文件
            for d in [IMAGES_TRAIN_DIR, IMAGES_VAL_DIR, LABELS_VAL_DIR]:
                for old in d.glob("*"):
                    old.unlink()
            # 收集所有已审查的截图
            import random, shutil
            reviewed_images = []
            for f in SCREENSHOTS_DIR.glob("*"):
                if f.suffix.lower() in (".png", ".jpg", ".jpeg") and f.stem in reviewed:
                    reviewed_images.append(f)
            random.shuffle(reviewed_images)
            split = int(len(reviewed_images) * 0.8)
            train_imgs = reviewed_images[:split]
            val_imgs = reviewed_images[split:]
            for img in train_imgs:
                shutil.copy2(str(img), str(IMAGES_TRAIN_DIR / img.name))
            for img in val_imgs:
                shutil.copy2(str(img), str(IMAGES_VAL_DIR / img.name))
                # 复制对应的标注到 val
                label_path = LABELS_TRAIN_DIR / f"{img.stem}.txt"
                if label_path.exists():
                    shutil.copy2(str(label_path), str(LABELS_VAL_DIR / label_path.name))
            self._log_yolo(f"数据集: train={len(train_imgs)} val={len(val_imgs)} (共 {len(reviewed)} 张已审核)")
        else:
            self._log_yolo("警告: 没有已审查图片，训练集可能为空！")

        classes = [line.strip() for line in
                   self._yolo_classes_text.toPlainText().split("\n")
                   if line.strip()]
        if not classes:
            classes = ["怪物"]

        content = f"""# YOLO 数据集配置 — 仅包含已审查图片
path: {DATASET_DIR.as_posix()}
train: images/train
val: images/val
nc: {len(classes)}
names:"""
        for i, name in enumerate(classes):
            content += f"\n  {i}: {name}"

        DATA_YAML.write_text(content, encoding="utf-8")
        return DATA_YAML

    # ============================================================
    # 训练
    # ============================================================
    def _run_yolo_train(self) -> None:
        # 检查未审核标注
        labeled = {f.stem for f in LABELS_TRAIN_DIR.glob("*.txt")}
        reviewed = load_reviewed_stems()
        unreviewed_labels = labeled - reviewed
        if unreviewed_labels:
            QMessageBox.warning(self.app, "警告",
                f"有 {len(unreviewed_labels)} 张图片已标注但未审核！\n"
                f"训练只会使用已审核的 {len(reviewed)} 张。\n"
                f"未审核的数据不会参与训练。")
            self._log_yolo(f"⚠ 跳过 {len(unreviewed_labels)} 张未审核标注")
        if not reviewed:
            QMessageBox.critical(self.app, "错误", "没有已审核的图片，无法训练")
            return

        yaml_path = self.generate_data_yaml()
        model_path = PROJECT_DIR / self._yolo_model_var.text()
        if not model_path.exists():
            QMessageBox.critical(self.app, "错误", f"模型文件不存在: {model_path}")
            return

        train_script = SCRIPTS_DIR / "train.py"
        if not train_script.exists():
            QMessageBox.critical(self.app, "错误", f"训练脚本不存在: {train_script}")
            return

        device = self._get_selected_device()
        resolved = resolve_device(device)

        cmd = [
            str(YOLO_PYTHON), str(train_script),
            "--model", str(model_path),
            "--data", str(yaml_path),
            "--epochs", self._yolo_epochs_var.text(),
            "--batch", self._yolo_batch_var.text(),
            "--imgsz", self._yolo_imgsz_var.text(),
            "--lr0", self._yolo_lr_var.text(),
            "--mosaic", self._yolo_mosaic_var.text(),
            "--patience", self._yolo_patience_var.text(),
            "--device", resolved,
            "--project", str(OUTPUTS_DIR),
        ]

        self._yolo_status_var.setText("训练中...")
        self._log_yolo(f"[GPU] {get_gpu_status_text()}")
        self._log_yolo(f"开始训练 (设备={device}→{resolved})")

        def run():
            try:
                # 1. 训练
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace",
                                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                for line in proc.stdout:
                    self.log_signal.emit(line.rstrip())
                proc.wait()
                self.log_signal.emit("=== 训练完成 ===")

                # 2. 自动验证 (找最新的 v{N}.pt)
                all_versions = sorted(
                    [f for f in MODELS_DIR.glob("v*.pt") if f.stem[1:].isdigit()],
                    key=lambda f: int(f.stem[1:])
                )
                if all_versions:
                    latest = all_versions[-1]
                    self.log_signal.emit(f"=== 自动验证: {latest.name} ===")
                    val_cmd = [
                        str(YOLO_PYTHON), str(SCRIPTS_DIR / "validate.py"),
                        "--weights", str(latest),
                        "--data", str(yaml_path),
                        "--device", device,
                    ]
                    vproc = subprocess.Popen(val_cmd, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT,
                                             text=True, encoding="utf-8", errors="replace",
                                             env={**os.environ, "PYTHONUNBUFFERED": "1"},
                                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                    map50_line = ""
                    precision_line = ""
                    recall_line = ""
                    for line in vproc.stdout:
                        self.log_signal.emit(line.rstrip())
                        if "mAP@50" in line:
                            map50_line = line
                        elif "Precision:" in line:
                            precision_line = line
                        elif "Recall" in line and ":" in line:
                            recall_line = line
                    vproc.wait()
                    _save_metrics(latest, map50_line, precision_line, recall_line,
                                  len(reviewed), self._yolo_epochs_var.text())

                self.status_signal.emit(f"训练完成")
            except Exception as e:
                self.log_signal.emit(f"异常: {e}")
                self.status_signal.emit("训练失败")

        threading.Thread(target=run, daemon=True).start()

    def _show_stats(self) -> None:
        """弹出训练统计图。"""
        import json, tempfile, subprocess, os, textwrap
        if not METRICS_FILE.exists():
            QMessageBox.information(self.app, "提示", "暂无训练记录")
            return
        data = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
        versions = sorted(data.keys(), key=lambda v: int(v[1:]))
        mAPs = [data[v]["map50"] * 100 for v in versions]
        precs = [data[v].get("precision", 0) * 100 for v in versions]
        recalls = [data[v].get("recall", 0) * 100 for v in versions]
        counts = [data[v]["images"] for v in versions]

        img_path = Path(tempfile.gettempdir()) / "yolo_train_stats.png"
        chart_script = img_path.with_suffix(".py")
        chart_script.write_text(textwrap.dedent("""\
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False
versions = """ + repr(versions) + """
mAPs = """ + repr(mAPs) + """
precs = """ + repr(precs) + """
recalls = """ + repr(recalls) + """
counts = """ + repr(counts) + """
METRICS_FILE = """ + repr(str(METRICS_FILE.resolve())) + """
data = json.loads(Path(METRICS_FILE).read_text(encoding='utf-8'))
fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(11, 7))
ax1.bar(versions, mAPs, color='#4CAF50'); ax1.set_title('mAP50 (%)'); ax1.set_ylabel('%'); ax1.set_ylim(0, 100)
for i, v in enumerate(mAPs): ax1.text(i, v + 1, '%.1f%%' % v, ha='center', fontsize=9)
x = list(range(len(versions))); w = 0.35
ax2.bar([i - w/2 for i in x], precs, w, label='精确率', color='#FF9800')
ax2.bar([i + w/2 for i in x], recalls, w, label='召回率', color='#2196F3')
ax2.set_title('精确率 vs 召回率 (%)'); ax2.set_ylabel('%'); ax2.set_ylim(0, 100)
ax2.set_xticks(x); ax2.set_xticklabels(versions); ax2.legend()
for i, (pr, rc) in enumerate(zip(precs, recalls)):
    ax2.text(i - w/2, pr + 1, '%.0f' % pr, ha='center', fontsize=7)
    ax2.text(i + w/2, rc + 1, '%.0f' % rc, ha='center', fontsize=7)
ax3.plot(versions, counts, 'o-', color='#9C27B0', markersize=8)
ax3.set_title('训练图片数'); ax3.set_ylabel('张')
for i, c in enumerate(counts): ax3.text(i, c + 10, str(c), ha='center', fontsize=9)
lines = []
for v in versions:
    d = data[v]
    lines.append('%s: mAP=%.1f%% P=%.1f%% R=%.1f%% (%d张)' % (v, d['map50']*100, d.get('precision',0)*100, d.get('recall',0)*100, d['images']))
ax4.axis('off'); ax4.text(0.05, 0.95, chr(10).join(lines), transform=ax4.transAxes, fontsize=10, verticalalignment='top', fontfamily='monospace')
plt.tight_layout()
plt.savefig(r'""" + str(img_path.resolve()) + """', dpi=100)
print('OK')
"""), encoding="utf-8")

        try:
            subprocess.run([str(YOLO_PYTHON), str(chart_script)], check=True,
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PYTHONUNBUFFERED": "1"})
            if img_path.exists():
                from PySide6.QtGui import QPixmap
                dlg = QDialog(self.app)
                dlg.setWindowTitle("训练统计")
                dlg.resize(1000, 600)
                layout = QVBoxLayout(dlg)
                lbl = QLabel()
                lbl.setPixmap(QPixmap(str(img_path)))
                layout.addWidget(lbl)
                dlg.show()
            else:
                QMessageBox.warning(self.app, "错误", "统计图生成失败")
        except Exception as e:
            QMessageBox.warning(self.app, "错误", f"统计图生成失败: {e}")
    def _run_yolo_label(self) -> None:
        # 优先使用选择的模型，否则用训练输出的 best.pt
        selected_model = self._yolo_label_model_combo.currentData()
        if selected_model:
            best_pt = Path(selected_model)
        else:
            best_pt = OUTPUTS_DIR / "results" / "train" / "weights" / "best.pt"
        if not best_pt.exists():
            QMessageBox.critical(self.app, "错误", f"模型不存在: {best_pt}")
            return

        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        if not images:
            QMessageBox.information(self.app, "提示", "图片池为空")
            return

        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        labeled_stems = ({f.stem for f in LABELS_TRAIN_DIR.glob("*.txt")}
                         | {f.stem for f in LABELS_VAL_DIR.glob("*.txt")})
        batch = [img for img in images if img.stem not in labeled_stems]

        if not batch:
            self._log_yolo("没有未标注的图片")
            return

        auto_label_script = SCRIPTS_DIR / "auto_label.py"
        device = self._get_selected_device()
        resolved = resolve_device(device)

        # 标注脚本自己处理设备解析（auto_label.py 内有 should_use_onnx 逻辑）
        # 传递原始设备值而非 resolved，让脚本自己决定用 ONNX还是 PT
        cmd = [
            str(YOLO_PYTHON), str(auto_label_script),
            "--weights", str(best_pt),
            "--source", str(out_dir),
            "--output", str(LABELS_TRAIN_DIR),
            "--conf", self._yolo_review_var.text(),
            "--img-width", str(TARGET_W),
            "--img-height", str(TARGET_H),
            "--device", device,
            "--skip-existing",
        ]

        self._yolo_status_var.setText(f"YOLO 标注中... ({len(batch)} 张)")
        self._log_yolo(f"[GPU] {get_gpu_status_text()}")
        self._log_yolo(f"YOLO 自动标注: {len(batch)} 张 (设备={device}→{resolved})")

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
                self.log_signal.emit(
                    f"YOLO 标注完成: {len(batch)} 张, 已保存到 labels/train/")
                self.status_signal.emit(
                    f"完成 {len(batch)} 张, 可审核")
                self.refresh_signal.emit()
                self.review_signal.emit(batch)
            except Exception as e:
                self.log_signal.emit(f"YOLO 标注异常: {e}")
                self.status_signal.emit("标注失败")

        threading.Thread(target=run, daemon=True).start()

    def _review_yolo_batch(self, images: list[Path]) -> None:
        # 传入全部图片 + 已审查集合，弹窗内部分屏显示
        reviewed = load_reviewed_stems()
        unreviewed_count = len([img for img in images if img.stem not in reviewed])
        if unreviewed_count == 0:
            self._log_yolo(f"全部 {len(images)} 张已审查，可切换「已审查」查看")

        def on_reviewed(stems: set[str]):
            if stems:
                save_reviewed_stems(stems)
                save_review_round(stems)
            self.app._refresh_pool_stats()

        dialog = ReviewDialog(self.app, images, LABELS_TRAIN_DIR, LABELS_VAL_DIR,
                              on_close=on_reviewed, reviewed_stems=reviewed)
        dialog.exec()

    # ============================================================
    # Helpers
    # ============================================================
    def _log_yolo(self, msg: str) -> None:
        self._yolo_log.appendPlainText(msg)

    def _get_screenshot_dir(self) -> Path:
        return Path(self.app._get_screenshot_dir())

    def _ui_invoke(self, fn):
        """从子线程安全调用 UI（废弃，改用 Signal emit）。"""
        pass
