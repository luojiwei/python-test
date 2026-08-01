"""yolo_tab.py — Tab 3: YOLO 自举训练 + 自动标注"""

import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from config import (PROJECT_DIR, SCREENSHOTS_DIR, DATASET_DIR, IMAGES_TRAIN_DIR,
                    IMAGES_VAL_DIR, LABELS_TRAIN_DIR, LABELS_VAL_DIR, DATA_YAML,
                    OUTPUTS_DIR, MODELS_DIR, SCRIPTS_DIR, YOLO_PYTHON, TARGET_W, TARGET_H)
from review_dialog import ReviewDialog


class YOLOTab:
    """Tab 3 — YOLO 自举训练: 训练 YOLO → 自动标注 → 低置信审核 → 重训循环"""

    def __init__(self, app) -> None:
        self.app = app

    def build(self, parent: ttk.Frame) -> None:
        f = parent
        pad = {"padx": 12, "pady": 4}

        # 说明
        info = ttk.LabelFrame(f, text="阶段说明")
        info.pack(fill="x", **pad)
        ttk.Label(info, text="用已审核的标注训练 YOLO，然后用 YOLO 自动标注新图片。\n"
                  "高置信度(>0.8)自动通过，低置信度弹窗审核。每 500 张重训一轮。",
                  justify="left").pack(**pad)

        # 训练参数
        row0 = ttk.Frame(f)
        row0.pack(fill="x", **pad)
        ttk.Label(row0, text="模型:").pack(side="left")
        self._yolo_model_var = tk.StringVar(value="yolo11n.pt")
        ttk.Entry(row0, textvariable=self._yolo_model_var, width=20).pack(side="left", padx=4)
        ttk.Label(row0, text="  epochs:").pack(side="left")
        self._yolo_epochs_var = tk.StringVar(value="50")
        ttk.Entry(row0, textvariable=self._yolo_epochs_var, width=6).pack(side="left", padx=4)
        ttk.Label(row0, text="  batch:").pack(side="left")
        self._yolo_batch_var = tk.StringVar(value="16")
        ttk.Entry(row0, textvariable=self._yolo_batch_var, width=6).pack(side="left", padx=4)
        ttk.Label(row0, text="  imgsz:").pack(side="left")
        self._yolo_imgsz_var = tk.StringVar(value="640")
        ttk.Entry(row0, textvariable=self._yolo_imgsz_var, width=6).pack(side="left", padx=4)

        # 自举参数
        row1 = ttk.Frame(f)
        row1.pack(fill="x", **pad)
        ttk.Label(row1, text="高置信直通阈值:").pack(side="left")
        self._yolo_auto_var = tk.StringVar(value="0.8")
        ttk.Entry(row1, textvariable=self._yolo_auto_var, width=6).pack(side="left", padx=4)
        ttk.Label(row1, text="  低置信审查阈值:").pack(side="left")
        self._yolo_review_var = tk.StringVar(value="0.3")
        ttk.Entry(row1, textvariable=self._yolo_review_var, width=6).pack(side="left", padx=4)
        ttk.Label(row1, text="  每轮重训张数:").pack(side="left", padx=(8, 0))
        self._yolo_retrain_var = tk.StringVar(value="500")
        ttk.Entry(row1, textvariable=self._yolo_retrain_var, width=6).pack(side="left", padx=4)

        # 类别名称
        row2 = ttk.Frame(f)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="类别名(每行一个):").pack(side="left")
        self._yolo_classes_text = tk.Text(row2, height=4, width=30,
                                          font=("Microsoft YaHei", 9))
        self._yolo_classes_text.pack(side="left", padx=4, fill="x", expand=True)
        self._yolo_classes_text.insert("1.0", "怪物\n绳子上\n绳子下\n梯子上\n梯子下")

        # 操作按钮
        row3 = ttk.Frame(f)
        row3.pack(fill="x", **pad)
        ttk.Button(row3, text="训练 YOLO", command=self._run_yolo_train).pack(side="left")
        ttk.Button(row3, text="YOLO 自动标注", command=self._run_yolo_label).pack(side="left", padx=4)
        ttk.Button(row3, text="验证模型", command=self._run_yolo_validate).pack(side="left", padx=4)
        ttk.Button(row3, text="保存模型", command=self._save_model).pack(side="left", padx=4)
        self._yolo_status_var = tk.StringVar(value="就绪")
        ttk.Label(row3, textvariable=self._yolo_status_var).pack(side="left", padx=8)

        # 输出日志
        self._yolo_log = tk.Text(f, height=10, bg="#1E1E1E", fg="#D4D4D4",
                                 font=("Consolas", 9), wrap="word")
        self._yolo_log.pack(fill="both", expand=True, **pad)
        scroll = ttk.Scrollbar(self._yolo_log, command=self._yolo_log.yview)
        scroll.pack(side="right", fill="y")
        self._yolo_log.config(yscrollcommand=scroll.set)

    # ============================================================
    # data.yaml 生成
    # ============================================================
    def generate_data_yaml(self) -> Path:
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_VAL_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)

        classes = [line.strip() for line in self._yolo_classes_text.get("1.0", "end").split("\n")
                   if line.strip()]
        if not classes:
            classes = ["怪物"]

        content = f"""# YOLO 数据集配置 — 自动标注工具生成
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
        yaml_path = self.generate_data_yaml()
        model_path = PROJECT_DIR / self._yolo_model_var.get()
        if not model_path.exists():
            messagebox.showerror("错误", f"模型文件不存在: {model_path}")
            return

        train_script = SCRIPTS_DIR / "train.py"
        if not train_script.exists():
            messagebox.showerror("错误", f"训练脚本不存在: {train_script}")
            return

        cmd = [
            str(YOLO_PYTHON), str(train_script),
            "--model", str(model_path),
            "--data", str(yaml_path),
            "--epochs", self._yolo_epochs_var.get(),
            "--batch", self._yolo_batch_var.get(),
            "--imgsz", self._yolo_imgsz_var.get(),
            "--device", "cpu",
            "--project", str(OUTPUTS_DIR),
        ]

        self._yolo_status_var.set("训练中...")
        self._log_yolo(f"开始训练: {' '.join(cmd)}")

        def run():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace")
                for line in proc.stdout:
                    self.app.root.after(0, lambda l=line.rstrip(): self._log_yolo(l))
                proc.wait()
                self.app.root.after(0, lambda: self._yolo_status_var.set(
                    f"训练完成 (epochs={self._yolo_epochs_var.get()})"))
                self.app.root.after(0, lambda: self._log_yolo("=== 训练完成 ==="))
            except Exception as e:
                self.app.root.after(0, lambda: self._log_yolo(f"训练异常: {e}"))
                self.app.root.after(0, lambda: self._yolo_status_var.set("训练失败"))

        threading.Thread(target=run, daemon=True).start()

    # ============================================================
    # YOLO 自动标注
    # ============================================================
    def _run_yolo_label(self) -> None:
        best_pt = OUTPUTS_DIR / "results" / "train" / "weights" / "best.pt"
        if not best_pt.exists():
            messagebox.showerror("错误", "请先训练 YOLO 模型")
            return

        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        if not images:
            messagebox.showinfo("提示", "图片池为空")
            return

        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        labeled_stems = {f.stem for f in LABELS_TRAIN_DIR.glob("*.txt")}
        unlabeled = [img for img in images if img.stem not in labeled_stems]

        retrain_size = (int(self._yolo_retrain_var.get())
                        if self._yolo_retrain_var.get().isdigit() else 500)
        batch = unlabeled[:retrain_size]

        if not batch:
            self._log_yolo("没有未标注的图片")
            return

        self._yolo_status_var.set(f"YOLO 标注中... ({len(batch)} 张)")
        self._log_yolo(f"YOLO 自动标注: {len(batch)} 张")

        def run():
            try:
                from ultralytics import YOLO
                yolo = YOLO(str(best_pt))
                review_thresh = float(self._yolo_review_var.get())

                for img_path in batch:
                    results = yolo(str(img_path), conf=review_thresh, verbose=False)
                    boxes = []
                    for r in results:
                        if r.boxes is None:
                            continue
                        for box in r.boxes:
                            cls_id = int(box.cls)
                            conf = float(box.conf)
                            xyxy = box.xyxy.tolist()[0]
                            x1, y1, x2, y2 = xyxy
                            w_img, h_img = float(TARGET_W), float(TARGET_H)
                            cx = ((x1 + x2) / 2) / w_img
                            cy = ((y1 + y2) / 2) / h_img
                            bw = (x2 - x1) / w_img
                            bh = (y2 - y1) / h_img
                            boxes.append((cls_id, cx, cy, bw, bh, conf))

                    label_path = LABELS_TRAIN_DIR / f"{img_path.stem}.txt"
                    lines = [f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"
                             for cls_id, cx, cy, bw, bh, conf in boxes]
                    label_path.write_text("\n".join(lines) + "\n" if lines else "",
                                          encoding="utf-8")

                self.app.root.after(0, lambda: self._log_yolo(
                    f"YOLO 标注完成: {len(batch)} 张, 已保存到 labels/train/"))
                self.app.root.after(0, lambda: self._yolo_status_var.set(
                    f"完成 {len(batch)} 张, 可审核"))
                self.app.root.after(0, self.app._refresh_pool_stats)
                self.app.root.after(0, lambda: self._review_yolo_batch(batch))
            except Exception as e:
                self.app.root.after(0, lambda: self._log_yolo(f"YOLO 标注异常: {e}"))
                self.app.root.after(0, lambda: self._yolo_status_var.set("标注失败"))

        threading.Thread(target=run, daemon=True).start()

    def _review_yolo_batch(self, images: list[Path]) -> None:
        dialog = ReviewDialog(self.app.root, images, LABELS_TRAIN_DIR, LABELS_VAL_DIR,
                              on_close=self.app._refresh_pool_stats)
        self.app.root.wait_window(dialog.top)

    # ============================================================
    # 验证
    # ============================================================
    def _run_yolo_validate(self) -> None:
        yaml_path = self.generate_data_yaml()
        validate_script = SCRIPTS_DIR / "validate.py"
        if not validate_script.exists():
            messagebox.showerror("错误", f"验证脚本不存在: {validate_script}")
            return

        cmd = [str(YOLO_PYTHON), str(validate_script),
               "--data", str(yaml_path),
               "--model", str(OUTPUTS_DIR / "results" / "train" / "weights" / "best.pt"),
               "--project", str(OUTPUTS_DIR)]

        self._yolo_status_var.set("验证中...")
        self._log_yolo(f"验证: {' '.join(cmd)}")

        def run():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace")
                for line in proc.stdout:
                    self.app.root.after(0, lambda l=line.rstrip(): self._log_yolo(l))
                proc.wait()
                self.app.root.after(0, lambda: self._yolo_status_var.set("验证完成"))
            except Exception as e:
                self.app.root.after(0, lambda: self._log_yolo(f"验证异常: {e}"))
                self.app.root.after(0, lambda: self._yolo_status_var.set("验证失败"))

        threading.Thread(target=run, daemon=True).start()

    # ============================================================
    # 保存模型
    # ============================================================
    def _save_model(self) -> None:
        best_pt = OUTPUTS_DIR / "results" / "train" / "weights" / "best.pt"
        if not best_pt.exists():
            messagebox.showerror("错误", "请先训练模型")
            return

        name = filedialog.asksaveasfilename(
            title="保存模型", initialdir=str(MODELS_DIR),
            defaultextension=".pt", filetypes=[("PyTorch", "*.pt")])
        if not name:
            return
        dest = Path(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(best_pt), str(dest))
        yaml_dest = dest.parent / "data.yaml"
        if DATA_YAML.exists():
            shutil.copy2(str(DATA_YAML), str(yaml_dest))
        messagebox.showinfo("保存成功", f"模型已保存到:\n{dest}\n\ndata.yaml → {yaml_dest}")

    # ============================================================
    # Helpers
    # ============================================================
    def _log_yolo(self, msg: str) -> None:
        self._yolo_log.insert("end", f"{msg}\n")
        self._yolo_log.see("end")

    def _get_screenshot_dir(self) -> Path:
        return Path(self.app._get_screenshot_dir())
