#!/usr/bin/env python3
"""
============================================================
YOLO 训练工具 — 截图、标记、训练一体化 GUI
============================================================
功能：
  1. 开始截图 — 窗口截图 + 自动分割 train/val + 记录图片清单
  2. 上传标记文件 — 批量上传 label txt + 按图片清单自动分发
  3. 开始训练 — YOLO 训练 / 验证 / 推理
============================================================
"""

import ctypes
import ctypes.wintypes
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
from pathlib import Path

# ============================================================
# 外部依赖 — 延迟导入，启动时不强制
# ============================================================
mss = None
Image = None

def _ensure_screenshot_libs():
    """确保截图所需库可用。"""
    global mss, Image
    if mss is None:
        try:
            import mss as _mss
            mss = _mss
        except ImportError:
            messagebox.showerror("缺少依赖", "请先安装 mss 库：\npip install mss")
            return False
    if Image is None:
        try:
            from PIL import Image as _Image
            Image = _Image
        except ImportError:
            messagebox.showerror("缺少依赖", "请先安装 Pillow 库：\npip install Pillow")
            return False
    return True


def _silent_dep_check():
    """启动时静默检查并自动安装依赖（不弹窗）。"""
    missing = []
    try:
        import mss  # noqa
    except ImportError:
        missing.append("mss")
    try:
        from PIL import Image  # noqa
    except ImportError:
        missing.append("Pillow")

    if not missing:
        return True

    # 静默安装缺失依赖
    py = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
    if py.exists():
        try:
            subprocess.check_call(
                [str(py), "-m", "pip", "install", *missing],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass
    return False


# ============================================================
# 配置
# ============================================================
PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "screenshots"
DATASET_DIR = PROJECT_DIR / "dataset"
IMAGES_TRAIN_DIR = DATASET_DIR / "images" / "train"
IMAGES_VAL_DIR = DATASET_DIR / "images" / "val"
LABELS_TRAIN_DIR = DATASET_DIR / "labels" / "train"
LABELS_VAL_DIR = DATASET_DIR / "labels" / "val"
TRAIN_LIST_FILE = DATASET_DIR / "train_list.json"
VAL_LIST_FILE = DATASET_DIR / "val_list.json"
DATA_YAML = DATASET_DIR / "data.yaml"
TRAIN_OUTPUT_DIR = PROJECT_DIR / "outputs"  # 训练结果输出目录
TRAINED_MODELS_DIR = PROJECT_DIR / "trained_models"  # 归档目录

WINDOW_TITLE = "WingsMs"
TARGET_W, TARGET_H = 1280, 720
IMAGE_FORMAT = "PNG"
INTERVAL = 1.0  # 截图间隔（秒）

TRAIN_RATIO = 0.8  # 80% 训练集

# YOLO 环境 — 用 python.exe 确保文件 I/O 正常，subprocess 端用 CREATE_NO_WINDOW 隐藏窗口
YOLO_PYTHON = Path(
    "C:/Users/Administrator/.workbuddy/binaries/python/envs/yolo/Scripts/python.exe"
)


# ============================================================
# 窗口操作（Windows API）
# ============================================================

def enum_visible_windows(min_width=100, min_height=100):
    """枚举所有可见窗口。"""
    found = []

    def callback(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < min_width or h < min_height:
            return True
        title = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, title, 256)
        if not title.value.strip():
            return True
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(
            (hwnd, title.value, rect.left, rect.top, rect.right, rect.bottom, pid.value)
        )
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), 0)
    found.sort(key=lambda x: (x[5] - x[3]) * (x[4] - x[2]), reverse=True)
    return found


def find_window_by_title(title: str):
    """按窗口标题模糊匹配。"""
    title_lower = title.lower()
    results = []
    for hwnd, wt, l, t, r, b, pid in enum_visible_windows():
        if title_lower in wt.lower():
            results.append((hwnd, wt, l, t, r, b, pid))
    return results


def force_foreground(hwnd: int):
    """强制将窗口提到最前。"""
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)
    cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    target_tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.c_ulong())
    attached = False
    if cur_tid != target_tid:
        ctypes.windll.user32.AttachThreadInput(cur_tid, target_tid, True)
        attached = True
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE
        )
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE
        )
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(cur_tid, target_tid, False)


def capture_and_save(sct, hwnd, output_dir: Path):
    """截取窗口当前区域，缩放到 720p 保存。返回文件名。"""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    if right <= left or bottom <= top:
        return None

    region = {
        "left": max(0, left),
        "top": max(0, top),
        "width": right - left,
        "height": bottom - top,
    }
    img_raw = sct.grab(region)
    img = Image.frombytes("RGB", img_raw.size, img_raw.bgra, "raw", "BGRX")
    img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    filename = f"shot_{timestamp}.{IMAGE_FORMAT.lower()}"
    filepath = output_dir / filename
    img.save(str(filepath), IMAGE_FORMAT)
    return filename


# ============================================================
# 窗口选择对话框
# ============================================================

def pick_window_dialog(root, windows, title="选择窗口"):
    """弹窗让用户从候选窗口中选一个。"""
    if len(windows) == 1:
        return 0

    win = tk.Toplevel(root)
    win.title(title)
    win.geometry("600x280")
    win.transient(root)
    win.grab_set()

    result = {"idx": None}

    tk.Label(
        win,
        text=f"找到 {len(windows)} 个窗口，请选择要截图的目标：",
        font=("Microsoft YaHei", 10),
    ).pack(pady=(10, 5))

    frame = tk.Frame(win)
    frame.pack(fill="both", expand=True, padx=10, pady=5)

    scrollbar = tk.Scrollbar(frame)
    scrollbar.pack(side="right", fill="y")

    listbox = tk.Listbox(
        frame,
        font=("Consolas", 9),
        yscrollcommand=scrollbar.set,
        width=80,
        height=8,
    )
    for i, (_, wt, l, t, r, b, pid) in enumerate(windows):
        w_px, h_px = r - l, b - t
        title_display = wt[:50] + ("..." if len(wt) > 50 else "")
        listbox.insert(tk.END, f"  [{i}] {title_display:<52}  {w_px}x{h_px:<10}  PID={pid}")
    listbox.pack(side="left", fill="both", expand=True)
    listbox.selection_set(0)
    scrollbar.config(command=listbox.yview)

    def ok():
        sel = listbox.curselection()
        if sel:
            result["idx"] = sel[0]
        win.destroy()

    def cancel():
        win.destroy()

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="确定", command=ok, width=10).pack(side="left", padx=5)
    tk.Button(btn_frame, text="取消", command=cancel, width=10).pack(side="left", padx=5)

    root.wait_window(win)
    return result["idx"]


# ============================================================
# 自动分割图片到 train/val
# ============================================================

def split_images_to_dataset(screenshot_dir: Path):
    """将截图按 80/20 比例分配到 images/train 和 images/val。"""
    images = sorted(
        [f for f in screenshot_dir.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg")]
    )
    if not images:
        return [], []

    shuffled = images[:]
    random.shuffle(shuffled)

    split_idx = max(1, int(len(shuffled) * TRAIN_RATIO))
    train_images = sorted(shuffled[:split_idx], key=lambda x: x.name)
    val_images = sorted(shuffled[split_idx:], key=lambda x: x.name)

    # 创建目标目录
    IMAGES_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_VAL_DIR.mkdir(parents=True, exist_ok=True)

    # 拷贝到 train
    for img in train_images:
        shutil.copy2(str(img), str(IMAGES_TRAIN_DIR / img.name))

    # 拷贝到 val
    for img in val_images:
        shutil.copy2(str(img), str(IMAGES_VAL_DIR / img.name))

    # 记录图片清单
    train_names = [img.name for img in train_images]
    val_names = [img.name for img in val_images]
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    with open(TRAIN_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(train_names, f, indent=2, ensure_ascii=False)

    with open(VAL_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(val_names, f, indent=2, ensure_ascii=False)

    return train_names, val_names


# ============================================================
# 标签文件分发
# ============================================================

def distribute_labels(label_files: list):
    """根据图片清单将 label txt 文件分发到 labels/train 和 labels/val。"""
    if not TRAIN_LIST_FILE.exists() or not VAL_LIST_FILE.exists():
        messagebox.showerror("错误", "图片清单不存在，请先截图并停止以生成清单。")
        return 0, 0

    with open(TRAIN_LIST_FILE, "r", encoding="utf-8") as f:
        train_names = json.load(f)
    with open(VAL_LIST_FILE, "r", encoding="utf-8") as f:
        val_names = json.load(f)

    # 构建 图片名（无后缀）→ 集合
    train_stems = {Path(n).stem for n in train_names}
    val_stems = {Path(n).stem for n in val_names}

    LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)

    train_count, val_count, unmatched_count = 0, 0, 0

    for path_str in label_files:
        txt_file = Path(path_str)
        if txt_file.suffix.lower() != ".txt":
            continue
        stem = txt_file.stem

        if stem in train_stems:
            shutil.copy2(str(txt_file), str(LABELS_TRAIN_DIR / txt_file.name))
            train_count += 1
        elif stem in val_stems:
            shutil.copy2(str(txt_file), str(LABELS_VAL_DIR / txt_file.name))
            val_count += 1
        else:
            unmatched_count += 1

    if unmatched_count > 0:
        messagebox.showwarning(
            "部分文件未匹配",
            f"有 {unmatched_count} 个 label 文件没有对应的图片，已忽略。",
        )

    return train_count, val_count


# ============================================================
# YOLO 训练运行器
# ============================================================

def run_yolo_script(script_name: str, args: list, log_widget: tk.Text):
    """在子进程中运行 YOLO 脚本，实时输出到 Text widget。"""
    yolo_dir = PROJECT_DIR
    script_path = yolo_dir / "scripts" / script_name

    if not script_path.exists():
        log_widget.insert(tk.END, f"[错误] 脚本不存在: {script_path}\n")
        return 1

    if not YOLO_PYTHON.exists():
        log_widget.insert(tk.END, f"[错误] YOLO Python 环境不存在: {YOLO_PYTHON}\n")
        return 1

    cmd = [str(YOLO_PYTHON), str(script_path)] + args
    log_widget.insert(tk.END, f"[运行] {' '.join(cmd)}\n\n")
    log_widget.see(tk.END)

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(yolo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in process.stdout:
            log_widget.insert(tk.END, line)
            log_widget.see(tk.END)
        process.wait()
        return process.returncode
    except Exception as e:
        log_widget.insert(tk.END, f"[异常] {e}\n")
        return 1


def generate_data_yaml(class_names: list):
    """生成 data.yaml 配置文件。"""
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    content = f"""# YOLO 数据集配置文件 — 由 YOLO训练工具 自动生成

path: {DATASET_DIR}
train: images/train
val: images/val

nc: {len(class_names)}

names:
"""
    for name in class_names:
        content += f"  - {name}\n"

    with open(DATA_YAML, "w", encoding="utf-8") as f:
        f.write(content)

    return DATA_YAML


# ============================================================
# 主 GUI
# ============================================================

class YOLOTrainerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.running = False
        self.thread = None
        self.count = 0
        self.target_hwnd = None
        self.target_title = WINDOW_TITLE

        root.title("YOLO 训练工具")
        root.geometry("560x700")
        root.resizable(False, False)

        # ---- Notebook (标签页) ----
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # 三个标签页
        self.tab_screenshot = ttk.Frame(self.notebook)
        self.tab_labels = ttk.Frame(self.notebook)
        self.tab_train = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_screenshot, text="  1. 开始截图  ")
        self.notebook.add(self.tab_labels, text="  2. 上传标记文件  ")
        self.notebook.add(self.tab_train, text="  3. 开始训练  ")

        self._build_screenshot_tab()
        self._build_labels_tab()
        self._build_train_tab()

        # 居中窗口
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"+{(sw - 560) // 2}+{(sh - 700) // 2}")

    # ========================
    # Tab 1: 截图
    # ========================

    def _build_screenshot_tab(self):
        tab = self.tab_screenshot

        # 说明
        tk.Label(
            tab,
            text="步骤1：截图收集",
            font=("Microsoft YaHei", 13, "bold"),
        ).pack(pady=(12, 2))

        tk.Label(
            tab,
            text="截图后自动按 80/20 分割到 train/val 文件夹",
            font=("Microsoft YaHei", 9),
            fg="#666",
        ).pack(pady=(0, 10))

        # 输出目录
        dir_frame = tk.Frame(tab)
        dir_frame.pack(fill="x", padx=20, pady=3)
        tk.Label(dir_frame, text="输出目录：", font=("Microsoft YaHei", 10)).pack(side="left")
        tk.Label(
            dir_frame,
            text=f"screenshots/",
            font=("Consolas", 9),
            fg="#888",
        ).pack(side="left", padx=5)
        tk.Button(
            dir_frame,
            text="打开",
            font=("Microsoft YaHei", 8),
            command=lambda: os.startfile(str(OUTPUT_DIR)),
        ).pack(side="left", padx=2)

        # 截图频率
        freq_frame = tk.Frame(tab)
        freq_frame.pack(fill="x", padx=20, pady=3)
        tk.Label(freq_frame, text="截图频率：", font=("Microsoft YaHei", 10)).pack(side="left")
        self.freq_var = tk.StringVar(value="1.0")
        tk.Entry(
            freq_frame, textvariable=self.freq_var, font=("Consolas", 10), width=6
        ).pack(side="left", padx=5)
        tk.Label(freq_frame, text="秒/张", font=("Microsoft YaHei", 9), fg="#888").pack(side="left")

        # 窗口标题
        title_frame = tk.Frame(tab)
        title_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(title_frame, text="窗口标题：", font=("Microsoft YaHei", 10)).pack(side="left")
        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        tk.Entry(
            title_frame, textvariable=self.window_title_var, font=("Microsoft YaHei", 10), width=20
        ).pack(side="left", padx=5)

        # 分割比例
        ratio_frame = tk.Frame(tab)
        ratio_frame.pack(fill="x", padx=20, pady=3)
        tk.Label(ratio_frame, text="训练/验证比例：", font=("Microsoft YaHei", 10)).pack(side="left")
        self.train_ratio_var = tk.StringVar(value="80")
        tk.Entry(ratio_frame, textvariable=self.train_ratio_var, width=4, font=("Consolas", 10)).pack(side="left")
        tk.Label(ratio_frame, text="/", font=("Microsoft YaHei", 10)).pack(side="left")
        self.val_ratio_var = tk.StringVar(value="20")
        tk.Entry(ratio_frame, textvariable=self.val_ratio_var, width=4, font=("Consolas", 10)).pack(side="left")
        tk.Label(ratio_frame, text="  (train / val)", font=("Microsoft YaHei", 9), fg="#888").pack(side="left")

        # 目标窗口信息
        self.lbl_target = tk.Label(
            tab, text=f"目标窗口: {self.target_title}",
            font=("Microsoft YaHei", 10), fg="#2980b9",
        )
        self.lbl_target.pack(pady=(8, 5))

        # 截图按钮
        self.screenshot_btn = tk.Button(
            tab,
            text="开始截图",
            font=("Microsoft YaHei", 14, "bold"),
            width=12,
            height=1,
            bg="#4ecdc4",
            fg="white",
            activebackground="#45b7af",
            relief="flat",
            cursor="hand2",
            command=self._toggle_screenshot,
        )
        self.screenshot_btn.pack(pady=8)

        # 状态
        self.lbl_screenshot_status = tk.Label(
            tab, text="就绪 — 点击按钮开始截图",
            font=("Microsoft YaHei", 9), fg="#555", wraplength=480,
        )
        self.lbl_screenshot_status.pack(pady=(5, 5))

        # 分割结果
        self.lbl_split_result = tk.Label(
            tab, text="",
            font=("Microsoft YaHei", 9), fg="#27ae60", wraplength=480,
        )
        self.lbl_split_result.pack(pady=(0, 8))

        # 当前图片列表预览
        self.train_list_text = tk.Text(tab, height=6, width=60, font=("Consolas", 8), state="disabled")
        self.train_list_text.pack(padx=20, pady=(0, 8), fill="both", expand=True)

    def _toggle_screenshot(self):
        if not self.running:
            self._start_screenshot()
        else:
            self._stop_screenshot()

    def _start_screenshot(self):
        # 清空之前的所有截图
        out_dir = OUTPUT_DIR
        if out_dir.exists():
            shutil.rmtree(str(out_dir))
        out_dir.mkdir(parents=True, exist_ok=True)

        # 查找窗口
        wt = self.window_title_var.get().strip()
        if wt:
            matches = find_window_by_title(wt)
            if matches:
                idx = pick_window_dialog(self.root, matches, f"匹配「{wt}」的窗口")
                if idx is None:
                    return
                self.target_hwnd, self.target_title, *_ = matches[idx]
                self.lbl_target.config(text=f"目标窗口: {self.target_title[:45]}")
                self._begin_capture(out_dir)
                return

        # 无匹配 — 扫描全部
        self.lbl_screenshot_status.config(text="正在扫描窗口，请在弹出的窗口中选择...", fg="#2980b9")
        self.root.update()
        all_windows = enum_visible_windows(min_width=80, min_height=80)

        if not all_windows:
            messagebox.showerror("错误", "未检测到任何可见窗口。")
            self.lbl_screenshot_status.config(text="就绪 — 未找到窗口", fg="#c0392b")
            return

        idx = pick_window_dialog(self.root, all_windows, "选择游戏窗口")
        if idx is None:
            self.lbl_screenshot_status.config(text="就绪 — 已取消", fg="#555")
            return

        self.target_hwnd, self.target_title, *_ = all_windows[idx]
        self.lbl_target.config(text=f"目标窗口: {self.target_title[:45]}")
        self._begin_capture(out_dir)

    def _begin_capture(self, out_dir: Path):
        if not _ensure_screenshot_libs():
            return

        try:
            force_foreground(self.target_hwnd)
        except Exception as e:
            print(f"置顶失败: {e}")
        time.sleep(0.3)

        self.running = True
        self.count = 0
        self.screenshot_btn.config(
            text="停止截图", bg="#ff6b6b", activebackground="#e85a5a"
        )
        self.lbl_screenshot_status.config(
            text=f"运行中... 0 张 | 输出: {out_dir}", fg="#27ae60"
        )
        self.lbl_split_result.config(text="")

        self.thread = threading.Thread(
            target=self._screenshot_loop, args=(out_dir,), daemon=True
        )
        self.thread.start()

    def _stop_screenshot(self):
        self.running = False
        self.screenshot_btn.config(
            text="开始截图", bg="#4ecdc4", activebackground="#45b7af"
        )
        self.lbl_screenshot_status.config(
            text=f"截图已停止 — 共 {self.count} 张 | 上传标记文件时将自动分配",
            fg="#27ae60",
        )

    def _screenshot_loop(self, out_dir: Path):
        with mss.mss() as sct:
            start = time.time()
            last_status = time.time()
            while self.running:
                t0 = time.time()
                try:
                    fname = capture_and_save(sct, self.target_hwnd, out_dir)
                    if fname:
                        self.count += 1
                except Exception as e:
                    self.root.after(0, self.lbl_screenshot_status.config,
                                    f"截图异常: {e}", "#c0392b")
                    break

                if time.time() - last_status > 0.5:
                    elapsed = time.time() - start
                    fps = self.count / elapsed if elapsed > 0 else 0
                    self.root.after(
                        0,
                        self.lbl_screenshot_status.config,
                        f"运行中... {self.count} 张 | FPS {fps:.1f} | 最新: {fname}",
                        "#27ae60",
                    )
                    last_status = time.time()

                try:
                    freq = float(self.freq_var.get())
                except ValueError:
                    freq = 1.0
                sleep_t = freq - (time.time() - t0)
                if sleep_t > 0:
                    time.sleep(sleep_t)

        elapsed = time.time() - start
        msg = f"截图已停止 | 共 {self.count} 张 | 耗时 {elapsed:.1f}s"
        self.root.after(0, self.lbl_screenshot_status.config, msg, "#555")

    def _do_split_images(self):
        """将截图按比例分配到 train/val。"""
        out_dir = OUTPUT_DIR

        try:
            train_pct = int(self.train_ratio_var.get())
            val_pct = int(self.val_ratio_var.get())
            if train_pct + val_pct != 100:
                raise ValueError
        except ValueError:
            messagebox.showerror("比例错误", "训练+验证比例之和必须为 100")
            self.lbl_screenshot_status.config(text="分割失败：比例不正确", fg="#c0392b")
            return

        global TRAIN_RATIO
        TRAIN_RATIO = train_pct / 100.0

        train_names, val_names = split_images_to_dataset(out_dir)

        # 更新显示
        total = len(train_names) + len(val_names)
        msg = (
            f"分割完成！共 {total} 张图片\n"
            f"  images/train/ : {len(train_names)} 张\n"
            f"  images/val/   : {len(val_names)} 张\n"
            f"清单已保存到:\n"
            f"  {TRAIN_LIST_FILE.name}\n"
            f"  {VAL_LIST_FILE.name}"
        )
        self.lbl_split_result.config(text=msg)

        # 更新预览
        self._update_train_list_preview(train_names, val_names)

        self.lbl_screenshot_status.config(
            text=f"截图完成 + 自动分割 | train:{len(train_names)} / val:{len(val_names)}",
            fg="#27ae60",
        )

    def _update_train_list_preview(self, train_names, val_names):
        self.train_list_text.config(state="normal")
        self.train_list_text.delete("1.0", tk.END)
        self.train_list_text.insert(tk.END, "=== 训练集 (train) ===\n")
        for n in train_names[:15]:
            self.train_list_text.insert(tk.END, f"  {n}\n")
        if len(train_names) > 15:
            self.train_list_text.insert(tk.END, f"  ... 共 {len(train_names)} 张\n")
        self.train_list_text.insert(tk.END, f"\n=== 验证集 (val) ===\n")
        for n in val_names[:5]:
            self.train_list_text.insert(tk.END, f"  {n}\n")
        if len(val_names) > 5:
            self.train_list_text.insert(tk.END, f"  ... 共 {len(val_names)} 张\n")
        self.train_list_text.config(state="disabled")

    # ========================
    # Tab 2: 上传标记文件
    # ========================

    def _build_labels_tab(self):
        tab = self.tab_labels

        tk.Label(
            tab,
            text="步骤2：上传标记文件",
            font=("Microsoft YaHei", 13, "bold"),
        ).pack(pady=(12, 2))

        tk.Label(
            tab,
            text="批量上传 YOLO 格式的 txt 标记文件，自动分发到 labels/train 和 labels/val",
            font=("Microsoft YaHei", 9),
            fg="#666",
            wraplength=480,
        ).pack(pady=(0, 10))

        # 清单检查
        checklist_frame = tk.Frame(tab)
        checklist_frame.pack(fill="x", padx=20, pady=5)

        self.lbl_train_count = tk.Label(
            checklist_frame,
            text="训练集 (train): —",
            font=("Microsoft YaHei", 10),
            fg="#2980b9",
        )
        self.lbl_train_count.pack(side="left", padx=(0, 20))

        self.lbl_val_count = tk.Label(
            checklist_frame,
            text="验证集 (val): —",
            font=("Microsoft YaHei", 10),
            fg="#27ae60",
        )
        self.lbl_val_count.pack(side="left")

        tk.Button(
            checklist_frame,
            text="刷新清单",
            font=("Microsoft YaHei", 8),
            command=self._refresh_checklist,
        ).pack(side="right")

        # 上传按钮
        upload_frame = tk.Frame(tab)
        upload_frame.pack(pady=15)
        self.upload_btn = tk.Button(
            upload_frame,
            text="上传标记文件 (txt)",
            font=("Microsoft YaHei", 13, "bold"),
            width=20,
            height=2,
            bg="#3498db",
            fg="white",
            activebackground="#2980b9",
            relief="flat",
            cursor="hand2",
            command=self._upload_labels,
        )
        self.upload_btn.pack()

        tk.Label(
            tab,
            text="选择包含 YOLO 标注 txt 文件的文件夹\n文件名需与截图文件名对应（如 shot_xxx.txt）",
            font=("Microsoft YaHei", 9),
            fg="#888",
        ).pack(pady=(5, 0))

        # 分发结果
        self.lbl_label_result = tk.Label(
            tab, text="",
            font=("Microsoft YaHei", 9), fg="#27ae60", wraplength=480,
        )
        self.lbl_label_result.pack(pady=(10, 5))

        # label 文件预览
        self.label_preview_text = tk.Text(
            tab, height=8, width=60, font=("Consolas", 8), state="disabled"
        )
        self.label_preview_text.pack(padx=20, pady=(0, 8), fill="both", expand=True)

    def _refresh_checklist(self):
        self._check_and_load_lists()
        if TRAIN_LIST_FILE.exists():
            with open(TRAIN_LIST_FILE, "r", encoding="utf-8") as f:
                train_names = json.load(f)
            self.lbl_train_count.config(text=f"训练集 (train): {len(train_names)} 张")
        if VAL_LIST_FILE.exists():
            with open(VAL_LIST_FILE, "r", encoding="utf-8") as f:
                val_names = json.load(f)
            self.lbl_val_count.config(text=f"验证集 (val): {len(val_names)} 张")

    def _check_and_load_lists(self):
        """检查图片清单是否存在。"""
        if not TRAIN_LIST_FILE.exists() or not VAL_LIST_FILE.exists():
            messagebox.showwarning(
                "缺少图片清单",
                "图片清单不存在。\n请先在「开始截图」中截图并停止以生成清单。",
            )
            return False
        return True

    def _upload_labels(self):
        files = filedialog.askopenfilenames(
            title="选择标记 txt 文件",
            filetypes=[("YOLO 标记文件", "*.txt")],
        )
        if not files:
            return

        self.lbl_label_result.config(text="正在处理...", fg="#2980b9")
        self.root.update_idletasks()

        # 1. 先检查图片是否需要分割
        if not TRAIN_LIST_FILE.exists() or not VAL_LIST_FILE.exists():
            self.log_text.insert(tk.END, "[自动分配] 截图图片尚未分割，正在按比例分配...\n")
            self.log_text.see(tk.END)
            try:
                train_pct = int(self.train_ratio_var.get())
                val_pct = int(self.val_ratio_var.get())
                if train_pct + val_pct != 100:
                    raise ValueError
            except ValueError:
                messagebox.showerror("比例错误", "训练+验证比例之和必须为 100")
                return
            global TRAIN_RATIO
            TRAIN_RATIO = train_pct / 100.0
            train_names, val_names = split_images_to_dataset(OUTPUT_DIR)
            self.lbl_train_count.config(text=f"训练集 (train): {len(train_names)} 张")
            self.lbl_val_count.config(text=f"验证集 (val): {len(val_names)} 张")
            self._update_train_list_preview(train_names, val_names)
            self.log_text.insert(tk.END, f"  train: {len(train_names)} 张, val: {len(val_names)} 张\n\n")
            self.log_text.see(tk.END)

        # 2. 分发标记文件
        train_count, val_count = distribute_labels(list(files))

        msg = (
            f"标记文件分发完成！\n"
            f"  labels/train/ : {train_count} 个\n"
            f"  labels/val/   : {val_count} 个"
        )
        self.lbl_label_result.config(text=msg)

        # 更新预览
        self._update_label_preview()

    def _update_label_preview(self):
        self.label_preview_text.config(state="normal")
        self.label_preview_text.delete("1.0", tk.END)

        if LABELS_TRAIN_DIR.exists():
            train_labels = sorted(LABELS_TRAIN_DIR.glob("*.txt"))
            self.label_preview_text.insert(tk.END, f"=== labels/train/ : {len(train_labels)} 个 ===\n")
            for f in train_labels[:8]:
                self.label_preview_text.insert(tk.END, f"  {f.name}\n")
            if len(train_labels) > 8:
                self.label_preview_text.insert(tk.END, f"  ... 共 {len(train_labels)} 个\n")

        if LABELS_VAL_DIR.exists():
            val_labels = sorted(LABELS_VAL_DIR.glob("*.txt"))
            self.label_preview_text.insert(tk.END, f"\n=== labels/val/ : {len(val_labels)} 个 ===\n")
            for f in val_labels[:4]:
                self.label_preview_text.insert(tk.END, f"  {f.name}\n")
            if len(val_labels) > 4:
                self.label_preview_text.insert(tk.END, f"  ... 共 {len(val_labels)} 个\n")

        self.label_preview_text.config(state="disabled")

    # ========================
    # Tab 3: 训练
    # ========================

    def _build_train_tab(self):
        tab = self.tab_train

        tk.Label(
            tab,
            text="步骤3：YOLO 训练",
            font=("Microsoft YaHei", 13, "bold"),
        ).pack(pady=(12, 2))

        # ---- 数据集配置 ----
        cfg_frame = tk.LabelFrame(tab, text="数据集配置", font=("Microsoft YaHei", 10))
        cfg_frame.pack(fill="x", padx=15, pady=(8, 4))

        # 类别名称（多行文本，一行一个）
        row1 = tk.Frame(cfg_frame)
        row1.pack(fill="x", padx=10, pady=3)
        tk.Label(row1, text="类别名称：", font=("Microsoft YaHei", 9), width=10, anchor="e").pack(side="left", anchor="n")
        self.class_text = tk.Text(
            row1, font=("Microsoft YaHei", 9), width=30, height=5,
            bg="#f5f5f0",
        )
        self.class_text.pack(side="left", padx=5, fill="x", expand=True)
        tk.Label(
            row1, text="每行一个类别\n训练时自动生成 data.yaml",
            font=("Microsoft YaHei", 8), fg="#888",
            justify="left",
        ).pack(side="left", anchor="n", padx=3)
        # 加载已有类别
        self._load_classes_to_text()

        # ---- 训练参数 ----
        param_frame = tk.LabelFrame(tab, text="训练参数", font=("Microsoft YaHei", 10))
        param_frame.pack(fill="x", padx=15, pady=4)

        params = [
            ("模型 (model):", "model_var", "yolo11n.pt", ["yolo11n.pt", "yolo11s.pt", "yolo11m.pt", "yolo11l.pt", "yolo11x.pt"]),
            ("训练轮数 (epochs):", "epochs_var", "100", None),
            ("批次大小 (batch):", "batch_var", "8", None),
            ("图片尺寸 (imgsz):", "imgsz_var", "640", None),
            ("设备 (device):", "device_var", "cpu", ["cpu", "0"]),
            ("学习率 (lr0):", "lr0_var", "0.01", None),
            ("Mosaic (mosaic):", "mosaic_var", "1.0", None),
            ("HSV-H (hsv_h):", "hsv_h_var", "0.015", None),
            ("HSV-S (hsv_s):", "hsv_s_var", "0.7", None),
            ("HSV-V (hsv_v):", "hsv_v_var", "0.4", None),
            ("随机缩放 (scale):", "scale_var", "0.5", None),
        ]

        self.param_vars = {}
        # 2 列布局
        for i, (label_text, var_name, default, options) in enumerate(params):
            row_idx = i // 2
            col_idx = i % 2
            row = tk.Frame(param_frame)
            row.grid(row=row_idx, column=col_idx, sticky="w", padx=10, pady=2)
            tk.Label(row, text=label_text, font=("Microsoft YaHei", 9), width=14, anchor="e").pack(side="left")

            if options:
                var = tk.StringVar(value=default)
                cb = ttk.Combobox(row, textvariable=var, values=options, width=10, font=("Consolas", 9))
                cb.pack(side="left", padx=5)
            else:
                var = tk.StringVar(value=default)
                tk.Entry(row, textvariable=var, font=("Consolas", 9), width=10).pack(side="left", padx=5)

            self.param_vars[var_name] = var

        # ---- 操作按钮 ----
        btn_frame = tk.Frame(tab)
        btn_frame.pack(pady=10)

        actions = [
            ("开始训练", "#4ecdc4", "#45b7af", self._run_train),
            ("验证模型", "#3498db", "#2980b9", self._run_validate),
            ("推理测试", "#9b59b6", "#8e44ad", self._run_predict),
            ("保存成果", "#27ae60", "#1e8449", self._show_save_dialog),
            ("重新训练", "#e74c3c", "#c0392b", self._reset_training),
        ]

        for text, bg, abg, cmd in actions:
            tk.Button(
                btn_frame,
                text=text,
                font=("Microsoft YaHei", 9, "bold"),
                width=8,
                height=1,
                bg=bg,
                fg="white",
                activebackground=abg,
                relief="flat",
                cursor="hand2",
                command=cmd,
            ).pack(side="left", padx=2, pady=3)

        # ---- 训练日志 ----
        log_frame = tk.LabelFrame(tab, text="运行日志", font=("Microsoft YaHei", 10))
        log_frame.pack(fill="both", expand=True, padx=15, pady=(2, 8))

        self.log_text = tk.Text(
            log_frame, height=16, font=("Consolas", 8),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
        )
        self.log_text.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)

        # 清空日志按钮
        tk.Button(
            tab,
            text="清空日志",
            font=("Microsoft YaHei", 8),
            command=lambda: self.log_text.delete("1.0", tk.END),
        ).pack(pady=(0, 5))

    def _load_classes_to_text(self):
        """从 data.yaml 加载已有类别到文本框。"""
        cats = []
        if DATA_YAML.exists():
            try:
                with open(DATA_YAML, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        if s.startswith("- "):
                            cats.append(s[2:].strip())
            except Exception:
                pass
        if cats:
            self.class_text.insert("1.0", "\n".join(cats))

    def _save_classes_from_text(self) -> list:
        """从文本框读取全量类别名称，生成 data.yaml。返回类别列表。"""
        raw = self.class_text.get("1.0", tk.END).strip()
        class_names = [c.strip() for c in raw.split("\n") if c.strip()]
        if not class_names:
            messagebox.showerror("错误", "请输入至少一个类别名称")
            return []
        generate_data_yaml(class_names)
        self.log_text.insert(tk.END, f"[data.yaml] 已更新: {len(class_names)} 个类别 {class_names}\n\n")
        self.log_text.see(tk.END)
        return class_names

    def _reset_training(self):
        """重新训练 — 清空 dataset、outputs、data.yaml。"""
        if not messagebox.askyesno(
            "重新训练确认",
            "此操作将清空以下内容：\n"
            "  • dataset/  (图片和标注)\n"
            "  • outputs/  (训练结果)\n"
            "  • data.yaml  (类别配置)\n\n"
            "所有数据将永久删除，不可恢复！\n\n确定要继续吗？",
            icon="warning",
        ):
            return

        self._set_buttons_state("disabled")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.insert(tk.END, "  重新训练 — 清空所有数据\n")
        self.log_text.insert(tk.END, "=" * 50 + "\n")

        dirs_to_clear = [DATASET_DIR, TRAIN_OUTPUT_DIR]
        for d in dirs_to_clear:
            if d.exists():
                try:
                    shutil.rmtree(str(d))
                    self.log_text.insert(tk.END, f"  已删除: {d}\n")
                except Exception as e:
                    self.log_text.insert(tk.END, f"  删除失败: {d} — {e}\n")

        if DATA_YAML.exists():
            try:
                DATA_YAML.unlink()
                self.log_text.insert(tk.END, f"  已删除: {DATA_YAML.name}\n")
            except Exception as e:
                self.log_text.insert(tk.END, f"  删除失败: {DATA_YAML.name} — {e}\n")

        self.class_text.delete("1.0", tk.END)
        self._set_buttons_state("normal")
        self.log_text.insert(tk.END, "\n[完成] 所有训练数据已清空，可以重新开始。\n\n")
        self.log_text.see(tk.END)

    def _show_save_dialog(self):
        """弹出保存成果对话框 — 输入地图名称后存到 trained_models/。"""
        best_pt = TRAIN_OUTPUT_DIR / "results" / "train" / "weights" / "best.pt"
        if not best_pt.exists():
            alt_best = list(TRAIN_OUTPUT_DIR.glob("**/best.pt"))
            if alt_best:
                best_pt = alt_best[0]
            else:
                messagebox.showinfo("提示", "未找到训练结果 best.pt，请先完成训练")
                return

        win = tk.Toplevel(self.root)
        win.title("保存训练成果")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        f = tk.Frame(win, padx=20, pady=15)
        f.pack()

        tk.Label(f, text="训练完成！", font=("Microsoft YaHei", 12, "bold"),
                 fg="#27ae60").pack(pady=(0, 10))

        tk.Label(f, text="输入地图名称，将训练成果存入 trained_models/：",
                 font=("Microsoft YaHei", 9)).pack()

        name_var = tk.StringVar()
        entry = tk.Entry(f, textvariable=name_var, font=("Microsoft YaHei", 11),
                         width=24, justify="center")
        entry.pack(pady=(8, 4))
        entry.focus_set()
        entry.bind("<Return>", lambda e: _confirm())

        tk.Label(f, text=f"目标: trained_models/<地图名>/  (best.pt + data.yaml)",
                 font=("Microsoft YaHei", 8), fg="#888").pack(pady=(0, 8))

        bf = tk.Frame(f)
        bf.pack()

        def _confirm():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提示", "请输入地图名称", parent=win)
                return
            self._do_save_trained_model(name, best_pt)
            win.destroy()

        tk.Button(bf, text="确认保存", font=("Microsoft YaHei", 10, "bold"),
                  width=10, bg="#27ae60", fg="white", cursor="hand2",
                  command=_confirm).pack(side="left", padx=4)

        tk.Button(bf, text="放弃", font=("Microsoft YaHei", 10),
                  width=8, bg="#95a5a6", fg="white", cursor="hand2",
                  command=win.destroy).pack(side="left", padx=4)

        self.root.wait_window(win)

    def _do_save_trained_model(self, map_name: str, best_pt: Path):
        """将 best.pt + data.yaml 复制到 trained_models/{map_name}/。"""
        dest_dir = TRAINED_MODELS_DIR / map_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 复制 best.pt
        shutil.copy2(str(best_pt), str(dest_dir / "best.pt"))
        self.log_text.insert(tk.END, f"  ✓ best.pt → {dest_dir / 'best.pt'}\n")

        # 复制 data.yaml
        if DATA_YAML.exists():
            with open(DATA_YAML, "r", encoding="utf-8") as f:
                yaml_content = f.read()
            # 修正路径为部署时的相对路径
            yaml_content = yaml_content.replace(
                str(DATASET_DIR).replace("\\", "/"),
                ".")
            dest_yaml = dest_dir / "data.yaml"
            with open(dest_yaml, "w", encoding="utf-8") as f:
                f.write(yaml_content)
            self.log_text.insert(tk.END, f"  ✓ data.yaml → {dest_yaml}\n")
        else:
            self.log_text.insert(tk.END, "  ⚠ data.yaml 不存在，仅保存了 best.pt\n")

        self.log_text.insert(tk.END, f"\n[保存] 训练成果已存入 trained_models/{map_name}/\n\n")
        self.log_text.see(tk.END)
        messagebox.showinfo("保存完成", f"训练成果已保存到:\ntrained_models/{map_name}/")

    def _run_train(self):
        # 训练前自动从文本框生成 data.yaml
        if not self._save_classes_from_text():
            return

        if not YOLO_PYTHON.exists():
            messagebox.showerror("错误", f"YOLO Python 环境不存在: {YOLO_PYTHON}")
            return

        self._set_buttons_state("disabled")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.insert(tk.END, "  开始 YOLOv11 训练\n")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.see(tk.END)

        model = self.param_vars["model_var"].get()
        epochs = self.param_vars["epochs_var"].get()
        batch = self.param_vars["batch_var"].get()
        imgsz = self.param_vars["imgsz_var"].get()
        device = self.param_vars["device_var"].get()
        lr0 = self.param_vars["lr0_var"].get()

        args = [
            "--model", model,
            "--data", str(DATA_YAML),
            "--project", str(TRAIN_OUTPUT_DIR),
            "--epochs", epochs,
            "--batch", batch,
            "--imgsz", imgsz,
            "--device", device,
            "--lr0", lr0,
            "--workers", "0",
            "--mosaic", self.param_vars["mosaic_var"].get(),
            "--hsv_h", self.param_vars["hsv_h_var"].get(),
            "--hsv_s", self.param_vars["hsv_s_var"].get(),
            "--hsv_v", self.param_vars["hsv_v_var"].get(),
            "--scale", self.param_vars["scale_var"].get(),
        ]

        thread = threading.Thread(
            target=self._run_training_thread, args=(args,), daemon=True
        )
        thread.start()

    def _run_training_thread(self, args):
        ret = run_yolo_script("train.py", args, self.log_text)
        self.root.after(0, self._set_buttons_state, "normal")
        if ret == 0:
            self.root.after(
                0,
                self.log_text.insert,
                tk.END,
                "\n[完成] 训练结束。模型保存在 outputs/results/train/weights/\n",
            )
            self.root.after(0, self.log_text.see, tk.END)
            self.root.after(500, self._show_save_dialog)
        else:
            self.root.after(
                0,
                self.log_text.insert,
                tk.END,
                f"\n[失败] 训练异常退出，返回码: {ret}\n",
            )

    def _run_validate(self):
        # 查找本地训练输出
        best_pt = TRAIN_OUTPUT_DIR / "results" / "train" / "weights" / "best.pt"

        if not best_pt.exists():
            alt_best = list(TRAIN_OUTPUT_DIR.glob("**/best.pt"))
            if alt_best:
                best_pt = alt_best[0]
            else:
                messagebox.showerror("错误", "未找到训练结果 best.pt，请先完成训练")
                return

        self._set_buttons_state("disabled")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.insert(tk.END, "  开始 YOLOv11 验证\n")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.see(tk.END)

        args = [
            "--weights", str(best_pt),
            "--data", str(DATA_YAML),
            "--project", str(TRAIN_OUTPUT_DIR),
            "--device", "cpu",
        ]
        thread = threading.Thread(
            target=self._run_validate_thread, args=(args,), daemon=True
        )
        thread.start()

    def _run_validate_thread(self, args):
        ret = run_yolo_script("validate.py", args, self.log_text)
        self.root.after(0, self._set_buttons_state, "normal")

    def _run_predict(self):
        # 查找本地训练输出
        best_pt = TRAIN_OUTPUT_DIR / "results" / "train" / "weights" / "best.pt"

        if not best_pt.exists():
            alt_best = list(TRAIN_OUTPUT_DIR.glob("**/best.pt"))
            if alt_best:
                best_pt = alt_best[0]
            else:
                messagebox.showerror("错误", "未找到训练结果 best.pt，请先完成训练")
                return

        source = filedialog.askopenfilenames(
            title="选择要推理的图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg"), ("所有文件", "*.*")],
        )
        if not source:
            return

        # 复制到临时目录（ultralytics 需要目录输入）
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix="yolo_predict_"))
        for f in source:
            shutil.copy2(f, str(tmp_dir / Path(f).name))
        source = str(tmp_dir)

        self._set_buttons_state("disabled")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.insert(tk.END, "  开始 YOLOv11 推理\n")
        self.log_text.insert(tk.END, "=" * 50 + "\n")
        self.log_text.see(tk.END)

        args = [
            "--weights", str(best_pt),
            "--source", source,
            "--project", str(TRAIN_OUTPUT_DIR),
            "--conf", "0.15",
            "--device", "cpu",
            "--noshow",
        ]
        thread = threading.Thread(
            target=self._run_predict_thread, args=(args, tmp_dir), daemon=True
        )
        thread.start()

    def _run_predict_thread(self, args, tmp_dir):
        ret = run_yolo_script("predict.py", args, self.log_text)
        # 清理临时目录
        try:
            shutil.rmtree(str(tmp_dir))
        except Exception:
            pass
        self.root.after(0, self._set_buttons_state, "normal")

    def _set_buttons_state(self, state):
        """设置训练选项卡按钮状态。"""
        for child in self.tab_train.winfo_children():
            if isinstance(child, tk.Frame):
                for widget in child.winfo_children():
                    if isinstance(widget, tk.Button):
                        try:
                            widget.config(state=state)
                        except tk.TclError:
                            pass


# ============================================================
# 入口
# ============================================================

def main():
    root = tk.Tk()
    YOLOTrainerApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        # 启动日志 — 写入桌面方便排查
        startup_log = Path(os.path.expanduser("~/Desktop")) / "yolo_trainer_startup.log"
        startup_log.write_text(f"=== YOLO Trainer Startup ===\nTime: {datetime.now()}\n", encoding="utf-8")
        # 静默检查并安装依赖
        _silent_dep_check()
        main()
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        # 同时写入日志文件和弹窗
        try:
            log_path = Path(os.path.expanduser("~/Desktop")) / "yolo_trainer_crash.log"
            log_path.write_text(f"CRASH at {datetime.now()}\n{err}", encoding="utf-8")
        except Exception:
            pass
        # 弹窗
        show_err = err[:800] if len(err) > 800 else err
        ctypes.windll.user32.MessageBoxW(0, show_err, "YOLO Training Tool - Startup Error", 0x10)
        raise
