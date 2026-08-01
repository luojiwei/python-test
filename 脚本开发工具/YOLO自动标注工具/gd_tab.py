"""gd_tab.py — Tab 2: Grounding DINO 粗标 + 审核"""

import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime
from pathlib import Path

from config import (SCREENSHOTS_DIR, LABELS_TRAIN_DIR, LABELS_VAL_DIR,
                    IMAGES_TRAIN_DIR, IMAGES_VAL_DIR, SCRIPTS_DIR, YOLO_PYTHON)
from review_dialog import ReviewDialog


class GDTab:
    """Tab 2 — GD 粗标审核：提示词配置 + GD 标注 + 弹窗审核 + 导出"""

    def __init__(self, app) -> None:
        self.app = app

    def build(self, parent: ttk.Frame) -> None:
        f = parent
        pad = {"padx": 12, "pady": 4}

        # 说明
        info = ttk.LabelFrame(f, text="阶段说明")
        info.pack(fill="x", **pad)
        ttk.Label(info, text="使用 trained_models/ 下所有 YOLO 模型合并标注，统一 class_id 输出。\n"
                  "标注完成后弹窗逐一审核。累计审核 500 张后建议切换到 Tab 3 自举加速。",
                  justify="left").pack(**pad)

        # 操作按钮
        row2 = ttk.Frame(f)
        row2.pack(fill="x", **pad)
        ttk.Button(row2, text="自动标注", command=self._run_gd_label).pack(side="left")
        ttk.Button(row2, text="审核上一批", command=self._review_last_batch).pack(side="left", padx=4)
        ttk.Button(row2, text="导出 YOLO 标注", command=self._export_gd_labels).pack(side="left", padx=4)
        self._gd_status_var = tk.StringVar(value="就绪")
        ttk.Label(row2, textvariable=self._gd_status_var).pack(side="left", padx=8)

        # 输出日志
        self._gd_log = tk.Text(f, height=12, bg="#1E1E1E", fg="#D4D4D4",
                               font=("Consolas", 9), wrap="word")
        self._gd_log.pack(fill="both", expand=True, **pad)
        scroll = ttk.Scrollbar(self._gd_log, command=self._gd_log.yview)
        scroll.pack(side="right", fill="y")
        self._gd_log.config(yscrollcommand=scroll.set)

    # ============================================================
    # GD 标注
    # ============================================================
    def _run_gd_label(self) -> None:
        """调用 GD 标注脚本 (subprocess -> scripts/gd_label.py)。"""
        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        if not images:
            messagebox.showinfo("提示", "图片池为空，请先截图")
            return

        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        labeled_stems = {f.stem for f in LABELS_TRAIN_DIR.glob("*.txt")}
        unlabeled = [img for img in images if img.stem not in labeled_stems]

        # 取前 200 张未标注的
        batch = unlabeled[:200]

        if not batch:
            messagebox.showinfo("提示", "当前批没有待标注图片")
            self._log_gd("所有图片已标注完毕")
            return

        self._gd_status_var.set(f"标注中... ({len(batch)} 张)")
        self._log_gd(f"开始多模型标注: {len(batch)} 张")

        label_script = SCRIPTS_DIR / "yolo_labeler.py"

        if not label_script.exists():
            self._log_gd("错误: scripts/yolo_labeler.py 不存在")
            self._gd_status_var.set("标注脚本缺失")
            return

        cmd = [
            str(YOLO_PYTHON), str(label_script),
            str(out_dir), str(LABELS_TRAIN_DIR),
        ]

        def run():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace")
                for line in proc.stdout:
                    self.app.root.after(0, lambda l=line.rstrip(): self._log_gd(l))
                proc.wait()
                self.app.root.after(0, lambda: self._log_gd(
                    f"GD 标注完成: {len(batch)} 张"))
                self.app.root.after(0, lambda: self._gd_status_var.set(
                    f"完成 {len(batch)} 张，可开始审核"))
                self.app.root.after(0, self.app._refresh_pool_stats)
                self.app.root.after(0, lambda: self._review_batch(batch))
            except FileNotFoundError:
                self.app.root.after(0, lambda: self._log_gd(
                    f"错误: Python 环境不可用 {YOLO_PYTHON}"))
                self.app.root.after(0, lambda: self._gd_status_var.set("环境错误"))
            except Exception as e:
                self.app.root.after(0, lambda: self._log_gd(f"标注异常: {e}"))
                self.app.root.after(0, lambda: self._gd_status_var.set("标注失败"))

        threading.Thread(target=run, daemon=True).start()

    def _review_last_batch(self) -> None:
        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        labeled = [img for img in images if (LABELS_TRAIN_DIR / f"{img.stem}.txt").exists()]
        batch = labeled[-200:] if labeled else []
        if not batch:
            messagebox.showinfo("提示", "没有可审核的批次")
            return
        self._review_batch(batch)

    def _review_batch(self, images: list[Path]) -> None:
        if not images:
            return
        dialog = ReviewDialog(self.app.root, images, LABELS_TRAIN_DIR, LABELS_VAL_DIR,
                              on_close=self.app._refresh_pool_stats)
        self.app.root.wait_window(dialog.top)

    def _export_gd_labels(self) -> None:
        import random

        out_dir = self._get_screenshot_dir()
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])

        # 先收集有标注的图片（在清空前）
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        labeled: list[Path] = []
        for img in images:
            label = LABELS_TRAIN_DIR / f"{img.stem}.txt"
            if label.exists():
                labeled.append(img)

        if not labeled:
            messagebox.showinfo("提示", "没有已标注的图片")
            return

        # 随机 80/20 切分
        random.shuffle(labeled)
        split = int(len(labeled) * 0.8)
        train_imgs = labeled[:split]
        val_imgs = labeled[split:]

        # 备份标注内容（清空前）
        label_backup: dict[str, str] = {}
        for lbl in LABELS_TRAIN_DIR.glob("*.txt"):
            label_backup[lbl.stem] = lbl.read_text(encoding="utf-8")

        # 清空旧 dataset
        IMAGES_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        IMAGES_VAL_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)
        for d in [IMAGES_TRAIN_DIR, IMAGES_VAL_DIR, LABELS_TRAIN_DIR, LABELS_VAL_DIR]:
            for f in d.iterdir():
                f.unlink()

        # 拷贝 images
        for img in train_imgs:
            shutil.copy2(str(img), str(IMAGES_TRAIN_DIR / img.name))
        for img in val_imgs:
            shutil.copy2(str(img), str(IMAGES_VAL_DIR / img.name))

        # 从备份写出 labels
        for img in train_imgs:
            text = label_backup.get(img.stem, "")
            (LABELS_TRAIN_DIR / f"{img.stem}.txt").write_text(text, encoding="utf-8")
        for img in val_imgs:
            text = label_backup.get(img.stem, "")
            (LABELS_VAL_DIR / f"{img.stem}.txt").write_text(text, encoding="utf-8")

        messagebox.showinfo("导出完成",
                            f"训练集: {len(train_imgs)} 张\n"
                            f"验证集: {len(val_imgs)} 张\n"
                            f"比例: {100*len(train_imgs)//len(labeled)}/{100*len(val_imgs)//len(labeled)}")

    def _log_gd(self, msg: str) -> None:
        ts = datetime.now().strftime("[%H:%M:%S]")
        self._gd_log.insert("end", f"{ts} {msg}\n")
        self._gd_log.see("end")

    def _get_screenshot_dir(self) -> Path:
        return Path(self.app._get_screenshot_dir())
