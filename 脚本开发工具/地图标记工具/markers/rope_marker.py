"""RopeMixin — 标记 Mixin 类。"""



import ctypes, json, os, threading, time, tkinter as tk
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    from .config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR
    from .drawing import draw_rope_preview
    from .player_detection import PlayerTracker, detect_player_dot
except ImportError:
    from config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR  # type: ignore[no-redef]
    from drawing import draw_rope_preview  # type: ignore[no-redef]
    from player_detection import PlayerTracker, detect_player_dot  # type: ignore[no-redef]


class RopeMixin:
    # ==================== 5. Rope ladder marking ====================

    def _on_rope_toggle(self) -> None:
        if self._mode == "platform":
            self.status_text.set("平台标记运行中，请先停止"); return
        if self._mode == "jump":
            self.status_text.set("跳跃点标记运行中，请先停止"); return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止"); return
        if self._mode == "rope": self._rope_stop()
        else: self._rope_start()

    def _rope_start(self) -> None:
        if not self._check_minimap_ready(): return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get()); mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get()); mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb); self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"绳梯标记中... 检测绳梯顶/底 (地图: {map_name})")
        self.rope_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None; self.frame_count = 0
        self.running = True; self._mode = "rope"
        self._rope_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="rope")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(target=self._loop_rope, args=(map_name,), daemon=True)
        self.thread.start()

    def _rope_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._rope_button_set_running(False)
        self._set_mode_buttons("normal"); self.confirm_btn.config(state="normal")
        self._mode = None
        if self.rope_detector.count > 0: self._rope_review_and_save()
        else: self.status_text.set("绳梯标记已停止 | 未检测到任何绳梯")

    def _rope_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["rope"]
        if running: btn.config(text="停止绳梯标记", bg="#ff6b6b", activebackground="#e85a5a")
        else: btn.config(text="绳梯标记", bg="#e67e22", activebackground="#d35400")

    def _loop_rope(self, map_name: str) -> None:
        import ctypes
        sct = mss.MSS()
        interval: float = 1.0 / CAPTURE_FPS
        last_status: float = time.time()
        while self.running:
            t0: float = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt: time.sleep(0.1); continue
                ml, mt, mr, mb = self.mm_offsets
                ml_abs: int = gl + ml; mt_abs: int = gt + mt
                mw: int = mr - ml; mh: int = mb - mt
                if mw <= 0 or mh <= 0: time.sleep(0.1); continue
                region: dict = {"left": ml_abs, "top": mt_abs, "width": mw, "height": mh}
                img_raw = sct.grab(region); mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None: self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None: self.rope_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now: float = time.time()
                if now - last_status > 0.5:
                    pending_hint: str = " | 等待底部..." if self.rope_detector.has_pending else ""
                    self.root.after(0, self.status_text.set,
                        f"绳梯标记中... {self.frame_count}帧 | "
                        f"已检测{self.rope_detector.count}条绳梯{pending_hint}")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}"); break
            sleep_t: float = interval - (time.time() - t0)
            if sleep_t > 0: time.sleep(sleep_t)
        msg: str = f"已停止  {self.frame_count}帧 | 检测到{self.rope_detector.count}条绳梯"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Rope Review & Save ====================

    def _rope_review_and_save(self) -> None:
        try: self._rope_review_and_save_impl()
        except Exception as e:
            import traceback
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_rope_error.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.status_text.set(f"绳梯审阅窗口错误: {e}")

    def _rope_review_and_save_impl(self) -> None:
        map_name: str = self.map_name_var.get().strip()
        yoff: int = self.rope_detector.y_offset
        new_ropes: list = [[tx, ty + yoff, bx, by + yoff]
                           for tx, ty, bx, by in self.rope_detector.ropes]
        old_ropes: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                old_ropes = json.load(f).get(map_name, {}).get("ropes", [])

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)
        state: dict = {"source": None, "idx": -1}
        rope_items: list = []; rope_labels: list[str] = []

        def _rebuild_items() -> None:
            nonlocal rope_items, rope_labels
            rope_items, rope_labels = [], []
            for i, r in enumerate(new_ropes):
                tx, ty, bx, by = r
                rope_items.append({"source": "new", "idx": i, "data": r})
                rope_labels.append(f"新{i + 1}: ({tx},{ty}) → ({bx},{by})")
            for i, r in enumerate(old_ropes):
                t: dict = r["top"]; b: dict = r["bottom"]
                rope_items.append({"source": "old", "idx": i, "data": r})
                rope_labels.append(f"旧{i + 1}: ({t['x']},{t['y']}) → ({b['x']},{b['y']})")
        _rebuild_items()

        def draw_preview() -> ImageTk.PhotoImage:
            img: Image.Image = draw_rope_preview(
                self._mm_snapshot, self.mm_size, map_name,
                new_ropes, old_ropes, state["source"], state["idx"],
                target_size=(dw, dh))
            return ImageTk.PhotoImage(img)

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅绳梯 - {map_name}")
        review_win.transient(self.root); review_win.grab_set()
        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        canvas.photo = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        def refresh_preview() -> None:
            canvas.photo = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)
        tk.Label(right_frame, text=f"绳梯标记: {len(rope_items)} 条",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(list_frame, font=("Consolas", 9), width=32,
                             height=14, selectmode="single", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scrollbar.pack(side="right", fill="y"); listbox.configure(yscrollcommand=scrollbar.set)

        def _fill_listbox() -> None:
            listbox.delete(0, "end")
            for label in rope_labels: listbox.insert("end", label)
        _fill_listbox()

        def on_listbox_select(_event=None) -> None:
            sel = listbox.curselection()
            if sel and sel[0] < len(rope_items):
                it = rope_items[sel[0]]
                state["source"], state["idx"] = it["source"], it["idx"]
            else: state["source"], state["idx"] = None, -1
            refresh_preview()
        listbox.bind("<<ListboxSelect>>", on_listbox_select)

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=6)
        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=4)

        def delete_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先在列表中选择一条绳梯"); return
            idx: int = sel[0]
            if idx >= len(rope_items): return
            it: dict = rope_items[idx]
            if it["source"] == "new": new_ropes.pop(it["idx"])
            else: old_ropes.pop(it["idx"])
            state["source"], state["idx"] = None, -1
            _rebuild_items(); _fill_listbox(); refresh_preview()

        def save_and_close() -> None:
            all_saved: list = []
            for r in new_ropes:
                tx, ty, bx, by = r
                all_saved.append({"top": {"x": tx, "y": ty}, "bottom": {"x": bx, "y": by}})
            all_saved.extend(old_ropes)
            self._rope_save(map_name, all_saved)
            review_win.destroy()

        def edit_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先在列表中选择一条绳梯"); return
            idx: int = sel[0]
            if idx >= len(rope_items): return
            it: dict = rope_items[idx]
            ew = tk.Toplevel(review_win)
            ew.title("编辑绳梯坐标"); ew.transient(review_win); ew.grab_set(); ew.resizable(False, False)
            if it["source"] == "new": tx, ty, bx, by = it["data"]
            else: t, b = it["data"]["top"], it["data"]["bottom"]; tx, ty, bx, by = t["x"], t["y"], b["x"], b["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="顶部坐标", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e", padx=(0, 4))
            top_x_var = tk.StringVar(value=str(tx)); tk.Entry(f, textvariable=top_x_var, width=6, font=("Consolas", 10)).grid(row=1, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e", padx=(0, 4))
            top_y_var = tk.StringVar(value=str(ty)); tk.Entry(f, textvariable=top_y_var, width=6, font=("Consolas", 10)).grid(row=2, column=1)
            tk.Label(f, text="底部坐标", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=4, column=0, sticky="e", padx=(0, 4))
            bot_x_var = tk.StringVar(value=str(bx)); tk.Entry(f, textvariable=bot_x_var, width=6, font=("Consolas", 10)).grid(row=4, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=5, column=0, sticky="e", padx=(0, 4))
            bot_y_var = tk.StringVar(value=str(by)); tk.Entry(f, textvariable=bot_y_var, width=6, font=("Consolas", 10)).grid(row=5, column=1)

            def _apply_edit() -> None:
                try: ntx, nty, nbx, nby = int(top_x_var.get()), int(top_y_var.get()), int(bot_x_var.get()), int(bot_y_var.get())
                except ValueError: self.status_text.set("坐标必须为整数"); return
                if it["source"] == "new": new_ropes[it["idx"]] = [ntx, nty, nbx, nby]
                else: old_ropes[it["idx"]] = {"top": {"x": ntx, "y": nty}, "bottom": {"x": nbx, "y": nby}}
                _rebuild_items(); _fill_listbox(); refresh_preview(); ew.destroy()

            btn_f = tk.Frame(f); btn_f.grid(row=6, column=0, columnspan=2, pady=(12, 0))
            tk.Button(btn_f, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_apply_edit).pack(side="left", padx=4)
            tk.Button(btn_f, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

        tk.Button(btn_frame, text="删除", font=("Microsoft YaHei", 10), width=8, bg="#e74c3c", fg="white", cursor="hand2", command=delete_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="编辑坐标", font=("Microsoft YaHei", 10), width=8, bg="#3498db", fg="white", cursor="hand2", command=edit_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"), width=8, bg="#4ecdc4", fg="white", cursor="hand2", command=save_and_close).pack(side="left", padx=3)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10), width=8, cursor="hand2", command=review_win.destroy).pack(side="left", padx=3)
        self.root.wait_window(review_win)

    def _rope_save(self, map_name: str, ropes: list) -> None:
        if not map_name: return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        existing: dict = data.get(map_name, {})
        existing["ropes"] = ropes
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "platforms" not in existing: existing["platforms"] = []
        if "jumps" not in existing: existing["jumps"] = []
        if "flash_points" not in existing: existing["flash_points"] = []
        data[map_name] = existing
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_text.set(f" {len(ropes)}条绳梯已保存至 {MAPS_FILE.name}")
