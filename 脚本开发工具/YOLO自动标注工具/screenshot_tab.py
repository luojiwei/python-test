"""screenshot_tab.py — Tab 1: 截图采集（照搬 YOLO训练工具 模式）"""

import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

import config
from config import (SCREENSHOTS_DIR, WINDOW_TITLE, TARGET_W, TARGET_H, INTERVAL,
                    IMAGES_TRAIN_DIR, IMAGES_VAL_DIR, LABELS_TRAIN_DIR,
                    LABELS_VAL_DIR, ensure_screenshot_libs)
from utils import (enum_visible_windows, find_window_by_title, force_foreground,
                   capture_and_save)


class ScreenshotTab:
    """Tab 1 — 截图采集"""

    def __init__(self, app) -> None:
        self.app = app

    def build(self, parent: ttk.Frame) -> None:
        f = parent
        pad = {"padx": 12, "pady": 4}

        # 窗口选择
        row = ttk.Frame(f)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="目标窗口:").pack(side="left")
        self._window_var = tk.StringVar(value=WINDOW_TITLE)
        ttk.Entry(row, textvariable=self._window_var, width=30).pack(side="left", padx=4)
        ttk.Button(row, text="选择窗口", command=self._pick_window).pack(side="left", padx=2)
        ttk.Button(row, text="浏览窗口", command=self._browse_windows).pack(side="left", padx=2)

        # 输出目录
        row2 = ttk.Frame(f)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="截图目录:").pack(side="left")
        self._screenshot_dir_var = tk.StringVar(value=str(SCREENSHOTS_DIR))
        ttk.Entry(row2, textvariable=self._screenshot_dir_var, width=40).pack(side="left", padx=4)
        ttk.Button(row2, text="浏览", command=self._browse_screenshot_dir).pack(side="left")

        # 地图名称 + 截图频率
        row3 = ttk.Frame(f)
        row3.pack(fill="x", **pad)
        ttk.Label(row3, text="地图名称:").pack(side="left")
        self._map_name_var = tk.StringVar(value="")
        ttk.Entry(row3, textvariable=self._map_name_var, width=16).pack(side="left", padx=4)
        ttk.Label(row3, text="  截图间隔(秒):").pack(side="left", padx=(12, 0))
        self._interval_var = tk.StringVar(value=str(INTERVAL))
        ttk.Entry(row3, textvariable=self._interval_var, width=8).pack(side="left", padx=4)

        # 控制按钮
        row4 = ttk.Frame(f)
        row4.pack(fill="x", **pad)
        self._screenshot_btn = ttk.Button(row4, text="开始截图", command=self._toggle_screenshot)
        self._screenshot_btn.pack(side="left")
        self._screenshot_status = tk.StringVar(value="就绪")
        ttk.Label(row4, textvariable=self._screenshot_status).pack(side="left", padx=8)

        # 图片池统计
        stat_frame = ttk.LabelFrame(f, text="图片池统计")
        stat_frame.pack(fill="x", padx=12, pady=4)
        self._pool_stats = tk.StringVar(value="图片池：0 张  |  已标注：0 张  |  待标：0 张")
        ttk.Label(stat_frame, textvariable=self._pool_stats).pack(**pad)
        ttk.Button(f, text="刷新统计", command=self.refresh_pool_stats).pack(pady=4)

    # ============================================================
    # 窗口选择
    # ============================================================
    def _pick_window(self) -> None:
        title = self._window_var.get().strip()
        if not title:
            messagebox.showwarning("提示", "请先输入窗口标题关键词")
            return
        windows = find_window_by_title(title)
        if not windows:
            messagebox.showinfo("提示", f"未找到包含 '{title}' 的窗口")
            return
        if len(windows) == 1:
            self.app._target_hwnd = windows[0][0]
            self._window_var.set(windows[0][1])
            return
        self._show_window_picker(windows)

    def _browse_windows(self) -> None:
        windows = enum_visible_windows(200, 200)
        if not windows:
            messagebox.showinfo("提示", "未找到可用窗口")
            return
        self._show_window_browser(windows)

    def _show_window_picker(self, windows: list) -> None:
        win = tk.Toplevel(self.app.root)
        win.title("选择窗口")
        win.geometry("600x280")
        win.transient(self.app.root)
        win.grab_set()
        tk.Label(win, text=f"找到 {len(windows)} 个窗口，请选择:",
                 font=("Microsoft YaHei", 10)).pack(pady=6)
        lb = tk.Listbox(win, font=("Consolas", 9), width=80, height=8)
        lb.pack(fill="both", expand=True, padx=10)
        for i, (_, wt, l, t, r, b, pid) in enumerate(windows):
            lb.insert(tk.END, f"  [{i}] {wt[:50]:<52}  {r-l}x{b-t:<10}  PID={pid}")
        lb.selection_set(0)
        def ok():
            sel = lb.curselection()
            if sel:
                idx = sel[0]
                self.app._target_hwnd = windows[idx][0]
                self._window_var.set(windows[idx][1])
            win.destroy()
        tk.Button(win, text="确定", command=ok, width=10).pack(pady=4)

    def _show_window_browser(self, windows: list) -> None:
        win = tk.Toplevel(self.app.root)
        win.title("浏览窗口")
        win.geometry("700x350")
        win.transient(self.app.root)
        win.grab_set()
        tk.Label(win, text=f"共 {len(windows)} 个窗口:",
                 font=("Microsoft YaHei", 10)).pack(pady=4)
        lb = tk.Listbox(win, font=("Consolas", 9), width=90, height=12)
        lb.pack(fill="both", expand=True, padx=10)
        for i, (_, wt, l, t, r, b, pid) in enumerate(windows):
            wt_short = wt[:55] + ("..." if len(wt) > 55 else "")
            lb.insert(tk.END, f"  [{i:02d}] {wt_short:<58}  {r-l}x{b-t:<10}  PID={pid}")
        def ok():
            sel = lb.curselection()
            if sel:
                idx = sel[0]
                self.app._target_hwnd = windows[idx][0]
                self._window_var.set(windows[idx][1])
            win.destroy()
        tk.Button(win, text="选择此窗口", command=ok, width=12).pack(pady=4)

    def _browse_screenshot_dir(self) -> None:
        d = filedialog.askdirectory(title="选择截图保存目录")
        if d:
            self._screenshot_dir_var.set(d)

    # ============================================================
    # 截图控制（照搬 YOLO训练工具 逻辑）
    # ============================================================
    def _toggle_screenshot(self) -> None:
        if self.app._screenshot_running:
            self._stop_screenshot()
        else:
            self._start_screenshot()

    def _start_screenshot(self) -> None:
        if not ensure_screenshot_libs():
            return

        # 找窗口
        title = self._window_var.get().strip()
        if not title:
            messagebox.showwarning("提示", "请先输入/选择目标窗口")
            return
        windows = find_window_by_title(title)
        if not windows:
            messagebox.showinfo("提示", f"未找到包含 '{title}' 的窗口")
            return
        self.app._target_hwnd = windows[0][0]

        out_dir = Path(self._screenshot_dir_var.get())
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            force_foreground(self.app._target_hwnd)
        except Exception:
            pass
        time.sleep(0.3)

        self.app._screenshot_running = True
        self.app._processed_count = 0
        self._screenshot_btn.config(text="停止截图")
        self._screenshot_status.set("运行中...")

        # 缓存地图名（子线程安全）
        map_name = self._map_name_var.get().strip()

        self.app._screenshot_thread = threading.Thread(
            target=self._screenshot_loop, args=(out_dir, map_name), daemon=True
        )
        self.app._screenshot_thread.start()

    def _stop_screenshot(self) -> None:
        self.app._screenshot_running = False
        self._screenshot_btn.config(text="开始截图")
        self._screenshot_status.set(f"已停止  共截 {self.app._processed_count} 张")

    def _screenshot_loop(self, out_dir: Path, map_name: str) -> None:
        with config.mss.mss() as sct:
            start = time.time()
            last_status = time.time()
            while self.app._screenshot_running:
                t0 = time.time()
                try:
                    fname = capture_and_save(sct, self.app._target_hwnd, out_dir, map_name)
                    if fname:
                        self.app._processed_count += 1
                except Exception as e:
                    self.app.root.after(0, lambda: self._screenshot_status.set(f"异常: {e}"))
                    break

                if time.time() - last_status > 0.5:
                    elapsed = time.time() - start
                    fps = self.app._processed_count / elapsed if elapsed > 0 else 0
                    self.app.root.after(0, lambda f=fps: self._screenshot_status.set(
                        f"运行中... {self.app._processed_count} 张  FPS {f:.1f}"
                    ))
                    last_status = time.time()

                try:
                    freq = float(self._interval_var.get())
                except ValueError:
                    freq = 1.0
                sleep_t = freq - (time.time() - t0)
                if sleep_t > 0:
                    time.sleep(sleep_t)

    # ============================================================
    # 统计
    # ============================================================
    def refresh_pool_stats(self) -> None:
        out_dir = Path(self._screenshot_dir_var.get())
        images = sorted([f for f in out_dir.iterdir()
                         if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
        LABELS_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        LABELS_VAL_DIR.mkdir(parents=True, exist_ok=True)
        total = len(images)
        labeled = (len(list(LABELS_TRAIN_DIR.glob("*.txt"))) +
                   len(list(LABELS_VAL_DIR.glob("*.txt"))))
        self._pool_stats.set(
            f"图片池：{total} 张  |  已标注：{labeled} 张  |  待标：{max(0, total - labeled)} 张")
