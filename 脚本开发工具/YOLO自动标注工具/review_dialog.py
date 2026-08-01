"""review_dialog.py — 标注审核弹窗（拖拽补框 / 列表管理 / 翻页审核）"""

import tkinter as tk
from tkinter import ttk
from pathlib import Path

CLASS_NAMES: dict[int, str] = {0: "怪物", 1: "绳子上", 2: "绳子下", 3: "梯子上", 4: "梯子下"}
CLASS_COLORS: list[str] = ["#00DD00", "#FF4444", "#FF8800", "#4488FF", "#AA00EE"]


class ReviewDialog:
    """弹窗审核标注结果。

    快捷键: Left/Right 翻页 | D 删除选中 | A 通过 | Esc 关闭 | 鼠标拖拽补框
    """

    def __init__(self, parent: tk.Tk, images: list[Path],
                 labels_train: Path, labels_val: Path,
                 on_close=None) -> None:
        self.parent = parent
        self.images = images
        self.labels_train = labels_train
        self.labels_val = labels_val
        self.on_close = on_close
        self._current_idx: int = 0
        self._modified: bool = False
        self._boxes: list[tuple[float, float, float, float, int]] = []
        self._drawing: bool = False
        self._draw_start: tuple[int, int] = (0, 0)
        self._img_tk = None
        self._img_id = None
        self._img_offset_x: int = 0
        self._img_offset_y: int = 0
        self._selected_idx: int = -1

        self.top = tk.Toplevel(parent)
        self.top.title(f"审核标注 ({len(images)} 张)")
        self.top.geometry("1100x720")
        self.top.transient(parent)
        self.top.grab_set()

        self._build_ui()
        self._load_image(0)

        self.top.bind("<Left>", lambda e: self._prev())
        self.top.bind("<Right>", lambda e: self._next())
        self.top.bind("<d>", lambda e: self._delete_selected())
        self.top.bind("<a>", lambda e: self._approve())
        self.top.bind("<Escape>", lambda e: self._close())

    # ============================================================
    # UI
    # ============================================================
    def _build_ui(self) -> None:
        # 顶部
        top = ttk.Frame(self.top)
        top.pack(fill="x", padx=8, pady=4)
        self._info_var = tk.StringVar(value="0 / 0")
        ttk.Label(top, textvariable=self._info_var,
                  font=("Microsoft YaHei", 10, "bold")).pack(side="left")
        self._status_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self._status_var, foreground="gray").pack(side="left", padx=12)

        btn_frame = ttk.Frame(top)
        btn_frame.pack(side="right")
        ttk.Button(btn_frame, text="通过 (A)", command=self._approve).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="保存并关闭", command=self._save_and_close).pack(side="left", padx=2)

        # 主体：左侧画布 + 右侧列表
        body = ttk.Frame(self.top)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        body.columnconfigure(0, weight=9)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=0)

        self._canvas = tk.Canvas(body, bg="#2D2D2D", cursor="crosshair")
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._canvas.bind("<Button-1>", self._on_mouse_down)
        self._canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)

        # 右侧标注列表（窄）
        side = ttk.Frame(body)
        side.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ttk.Label(side, text="标注列表", font=("Microsoft YaHei", 9, "bold")).pack(pady=2)

        list_frame = ttk.Frame(side)
        list_frame.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame)
        scroll.pack(side="right", fill="y")
        self._listbox = tk.Listbox(list_frame, yscrollcommand=scroll.set,
                                   font=("Microsoft YaHei", 8), width=10, height=18,
                                   exportselection=False)
        self._listbox.pack(side="left", fill="both", expand=True)
        scroll.config(command=self._listbox.yview)
        self._listbox.bind("<<ListboxSelect>>", self._on_list_select)

        # 列表操作（换行显示）
        list_btns = ttk.Frame(side)
        list_btns.pack(fill="x", pady=4)
        row1 = ttk.Frame(list_btns)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="改为:", font=("Microsoft YaHei", 8)).pack(side="left")
        self._class_var = tk.StringVar(value="怪物")
        ttk.Combobox(row1, textvariable=self._class_var,
                     values=list(CLASS_NAMES.values()), state="readonly", width=6).pack(side="left", padx=2)
        ttk.Button(row1, text="修改", command=self._change_class, width=5).pack(side="left", padx=2)
        row2 = ttk.Frame(list_btns)
        row2.pack(fill="x", pady=2)
        ttk.Button(row2, text="删除 (D)", command=self._delete_selected, width=12).pack(fill="x")

        # 底部导航
        bottom = ttk.Frame(self.top)
        bottom.pack(fill="x", padx=8, pady=4)
        ttk.Button(bottom, text="上一张 (Left)", command=self._prev).pack(side="left")
        ttk.Button(bottom, text="下一张 (Right)", command=self._next).pack(side="left", padx=4)
        ttk.Label(bottom, text="鼠标拖拽画框 | 列表点选 | D=删除 | A=通过 | Left/Right=翻页").pack(
            side="right", foreground="gray")

    # ============================================================
    # 图片加载
    # ============================================================
    def _load_image(self, idx: int) -> None:
        if not self.images:
            return
        self._current_idx = max(0, min(idx, len(self.images) - 1))
        img_path = self.images[self._current_idx]

        try:
            from PIL import Image, ImageTk
            pil_img = Image.open(str(img_path))
            canvas_w = self._canvas.winfo_width() or 780
            canvas_h = self._canvas.winfo_height() or 620
            ratio = min(canvas_w / pil_img.width, canvas_h / pil_img.height, 1.0)
            new_w, new_h = int(pil_img.width * ratio), int(pil_img.height * ratio)
            pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
            self._img_tk = ImageTk.PhotoImage(pil_img)
        except Exception:
            self._img_tk = None
            return

        self._canvas.delete("all")
        self._img_id = self._canvas.create_image(
            canvas_w // 2, canvas_h // 2, image=self._img_tk, anchor="center")
        self._img_offset_x = canvas_w // 2 - self._img_tk.width() // 2
        self._img_offset_y = canvas_h // 2 - self._img_tk.height() // 2

        self._boxes = []
        label_path = self.labels_train / f"{img_path.stem}.txt"
        if label_path.exists():
            for line in label_path.read_text(encoding="utf-8").strip().split("\n"):
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                cls_id = int(parts[0])
                cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                x1 = (cx - w/2) * self._img_tk.width() + self._img_offset_x
                y1 = (cy - h/2) * self._img_tk.height() + self._img_offset_y
                x2 = (cx + w/2) * self._img_tk.width() + self._img_offset_x
                y2 = (cy + h/2) * self._img_tk.height() + self._img_offset_y
                self._boxes.append((x1, y1, x2, y2, cls_id))

        self._selected_idx = -1
        self._draw_boxes()
        self._refresh_list()
        self._info_var.set(f"{self._current_idx + 1} / {len(self.images)}")
        self._status_var.set(f"文件: {img_path.name}  |  框数: {len(self._boxes)}")
        self._modified = False

    # ============================================================
    # 画布渲染
    # ============================================================
    def _draw_boxes(self) -> None:
        if self._img_id is None:
            return
        self._canvas.delete("box")
        colors = CLASS_COLORS
        for i, (x1, y1, x2, y2, cls_id) in enumerate(self._boxes):
            selected = (i == self._selected_idx)
            color = colors[cls_id % len(colors)]
            tag = f"box_{i}"
            if selected:
                # 选中框：黄色填充 + 粗红边框，非常突出
                self._canvas.create_rectangle(
                    x1, y1, x2, y2, fill="#FFFF44", stipple="gray50",
                    width=0, tags=("box", tag, "selected"))
                self._canvas.create_rectangle(
                    x1, y1, x2, y2, outline="#FF0000", width=4,
                    tags=("box", tag, "selected"))
            else:
                self._canvas.create_rectangle(
                    x1, y1, x2, y2, outline=color, width=2,
                    tags=("box", tag))
            name = CLASS_NAMES.get(cls_id, f"cls{cls_id}")
            self._canvas.create_text(x1 + 2, y1 + 2, text=name,
                                     anchor="nw", fill="#FF0000" if selected else color,
                                     font=("Microsoft YaHei", 9, "bold" if selected else "normal"),
                                     tags=("box", tag))

    # ============================================================
    # 右侧列表
    # ============================================================
    def _refresh_list(self) -> None:
        self._listbox.delete(0, "end")
        for i, (x1, y1, x2, y2, cls_id) in enumerate(self._boxes):
            name = CLASS_NAMES.get(cls_id, f"cls{cls_id}")
            self._listbox.insert("end", f"  {i+1}. {name}")
        if self._selected_idx >= 0 and self._selected_idx < len(self._boxes):
            self._listbox.selection_set(self._selected_idx)

    def _on_list_select(self, event) -> None:
        sel = self._listbox.curselection()
        if sel:
            self._selected_idx = sel[0]
            cls_id = self._boxes[self._selected_idx][4]
            self._class_var.set(CLASS_NAMES.get(cls_id, f"cls{cls_id}"))
        else:
            self._selected_idx = -1
        self._draw_boxes()

    def _change_class(self) -> None:
        if self._selected_idx < 0 or self._selected_idx >= len(self._boxes):
            return
        new_name = self._class_var.get()
        new_id = 0
        for kid, name in CLASS_NAMES.items():
            if name == new_name:
                new_id = kid
                break
        x1, y1, x2, y2, _ = self._boxes[self._selected_idx]
        self._boxes[self._selected_idx] = (x1, y1, x2, y2, new_id)
        self._refresh_list()
        self._draw_boxes()
        self._modified = True
        self._status_var.set(
            f"文件: {self.images[self._current_idx].name}  |  框数: {len(self._boxes)}  [已修改]")

    def _delete_selected(self) -> None:
        if self._selected_idx < 0 or self._selected_idx >= len(self._boxes):
            return
        self._boxes.pop(self._selected_idx)
        self._selected_idx = min(self._selected_idx, len(self._boxes) - 1)
        self._refresh_list()
        self._draw_boxes()
        self._modified = True
        self._status_var.set(
            f"文件: {self.images[self._current_idx].name}  |  框数: {len(self._boxes)}  [已修改]")

    # ============================================================
    # 鼠标画框
    # ============================================================
    def _on_mouse_down(self, event) -> None:
        self._drawing = True
        self._draw_start = (event.x, event.y)

    def _on_mouse_drag(self, event) -> None:
        if not self._drawing:
            return
        self._canvas.delete("draw_preview")
        x1, y1 = self._draw_start
        self._canvas.create_rectangle(x1, y1, event.x, event.y,
                                      outline="#FFFF00", width=2,
                                      tags="draw_preview", dash=(4, 2))

    def _on_mouse_up(self, event) -> None:
        if not self._drawing:
            return
        self._drawing = False
        self._canvas.delete("draw_preview")
        x1, y1 = self._draw_start
        x2, y2 = event.x, event.y
        if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
            return
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        self._boxes.append((x1, y1, x2, y2, 0))
        self._selected_idx = len(self._boxes) - 1
        self._refresh_list()
        self._draw_boxes()
        self._modified = True
        self._status_var.set(
            f"文件: {self.images[self._current_idx].name}  |  框数: {len(self._boxes)}  [已修改]")

    # ============================================================
    # 操作
    # ============================================================
    def _approve(self) -> None:
        self._save_current()
        self._next()

    def _save_current(self) -> None:
        if not self._modified or self._img_tk is None:
            return
        img_path = self.images[self._current_idx]
        label_path = self.labels_train / f"{img_path.stem}.txt"

        lines: list[str] = []
        for x1, y1, x2, y2, cls_id in self._boxes:
            cx = ((x1 + x2) / 2 - self._img_offset_x) / self._img_tk.width()
            cy = ((y1 + y2) / 2 - self._img_offset_y) / self._img_tk.height()
            w = abs(x2 - x1) / self._img_tk.width()
            h = abs(y2 - y1) / self._img_tk.height()
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))
            w = max(0.0001, min(1.0, w))
            h = max(0.0001, min(1.0, h))
            lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

        label_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        self._modified = False

    def _prev(self) -> None:
        self._save_current()
        if self._current_idx > 0:
            self._load_image(self._current_idx - 1)

    def _next(self) -> None:
        self._save_current()
        if self._current_idx < len(self.images) - 1:
            self._load_image(self._current_idx + 1)

    def _save_and_close(self) -> None:
        self._save_current()
        self._close()

    def _close(self) -> None:
        if self.on_close:
            self.on_close()
        self.top.destroy()
