"""FlashMixin — 标记 Mixin 类。"""



import ctypes, json, os, threading, time, tkinter as tk
import mss
import numpy as np
from PIL import Image, ImageTk

try:
    from .config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR
    from .drawing import draw_flash_preview
    from .player_detection import PlayerTracker, detect_player_dot
except ImportError:
    from config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR  # type: ignore[no-redef]
    from drawing import draw_flash_preview  # type: ignore[no-redef]
    from player_detection import PlayerTracker, detect_player_dot  # type: ignore[no-redef]


class FlashMixin:
    # ==================== 4. Flash / teleport marking ====================

    def _on_flash_toggle(self) -> None:
        if self._mode is not None and self._mode != "flash":
            self.status_text.set(f"{self._mode}标记运行中，请先停止"); return
        if self._mode == "flash": self._flash_stop()
        else: self._flash_start()

    def _flash_start(self) -> None:
        if not self._check_minimap_ready(): return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get()); mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get()); mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb); self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"闪现点标记中... (地图: {map_name})")
        self.flash_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None; self.frame_count = 0
        self.running = True; self._mode = "flash"
        self._flash_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="flash")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(target=self._loop_flash, args=(map_name,), daemon=True)
        self.thread.start()

    def _flash_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._flash_button_set_running(False)
        self._set_mode_buttons("normal"); self.confirm_btn.config(state="normal")
        self._mode = None
        if self.flash_detector.count > 0: self._flash_review_and_save()
        else: self.status_text.set("闪现点标记已停止 | 未检测到闪现")

    def _flash_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["flash"]
        if running: btn.config(text="停止闪现点标记", bg="#ff6b6b", activebackground="#e85a5a")
        else: btn.config(text="闪现点标记", bg="#f39c12", activebackground="#e67e22")

    def _loop_flash(self, map_name: str) -> None:
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
                if mr - ml <= 0 or mb - mt <= 0: time.sleep(0.1); continue
                region: dict = {"left": gl + ml, "top": gt + mt,
                                "width": mr - ml, "height": mb - mt}
                img_raw = sct.grab(region); mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None: self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None: self.flash_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"闪现点标记中... {self.frame_count}帧 | "
                        f"已检测{self.flash_detector.count}次闪现")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}"); break
            sleep_t = interval - (time.time() - t0)
            if sleep_t > 0: time.sleep(sleep_t)
        msg = f"已停止  {self.frame_count}帧 | 检测到{self.flash_detector.count}次闪现"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Flash Review & Save ====================

    def _flash_review_and_save(self) -> None:
        try: self._flash_review_and_save_impl()
        except Exception as e:
            import traceback
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_flash_error.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.status_text.set(f"闪现审阅窗口错误: {e}")

    def _flash_review_and_save_impl(self) -> None:
        map_name = self.map_name_var.get().strip()
        yoff = self.flash_detector.y_offset
        new_flash = [[fx, fy + yoff, tx, ty + yoff, dr]
                     for fx, fy, tx, ty, dr in self.flash_detector.flashes]
        old_flash = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                old_flash = json.load(f).get(map_name, {}).get("flash_points", [])
        sw, sh = self.mm_size
        scale = min(6.0, 700 / max(sw, sh, 1))
        dw, dh = int(sw * scale), int(sh * scale)
        state: dict = {"source": None, "idx": -1}
        items, labels = [], []

        def _rebuild():
            nonlocal items, labels
            items, labels = [], []
            for i, r in enumerate(new_flash):
                dr = r[4] if len(r) > 4 else "?"
                tp = r[5] if len(r) > 5 else "one_way"
                tp_label = "单向" if tp == "one_way" else "双向"
                items.append({"source": "new", "idx": i, "data": r})
                labels.append(f"新{i+1}: ({r[0]},{r[1]}) -> ({r[2]},{r[3]}) [{dr}][{tp_label}]")
            for i, r in enumerate(old_flash):
                frm, to = r["from"], r["to"]
                tp = r.get("type", "one_way")
                tp_label = "单向" if tp == "one_way" else "双向"
                items.append({"source": "old", "idx": i, "data": r})
                labels.append(f"旧{i+1}: ({frm['x']},{frm['y']}) -> ({to['x']},{to['y']}) [{tp_label}]")
        _rebuild()

        def draw_preview():
            return ImageTk.PhotoImage(draw_flash_preview(
                self._mm_snapshot, self.mm_size, map_name,
                new_flash, old_flash, state["source"], state["idx"],
                target_size=(dw, dh)))

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅闪现点 - {map_name}")
        review_win.transient(self.root); review_win.grab_set()
        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        canvas.photo = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        def refresh_preview():
            canvas.photo = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=canvas.photo)

        right = tk.Frame(review_win)
        right.pack(side="right", padx=10, pady=10, fill="both", expand=True)
        tk.Label(right, text=f"闪现点: {len(items)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        lf = tk.Frame(right); lf.pack(fill="both", expand=True)
        lb = tk.Listbox(lf, font=("Consolas", 9), width=32, height=14,
                        selectmode="single", exportselection=False)
        lb.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(lf, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y"); lb.configure(yscrollcommand=sb.set)

        def _fill(): lb.delete(0, "end"); [lb.insert("end", L) for L in labels]
        _fill()

        def on_sel(_e=None):
            s = lb.curselection()
            if s: it = items[s[0]]; state["source"], state["idx"] = it["source"], it["idx"]
            else: state["source"], state["idx"] = None, -1
            refresh_preview()
        lb.bind("<<ListboxSelect>>", on_sel)

        tk.Frame(right, height=1, bg="#ccc").pack(fill="x", pady=6)
        bf = tk.Frame(right); bf.pack(side="bottom", fill="x", pady=4)

        def del_sel():
            s = lb.curselection()
            if not s: self.status_text.set("请先选择"); return
            it = items[s[0]]
            if it["source"] == "new": new_flash.pop(it["idx"])
            else: old_flash.pop(it["idx"])
            state["source"], state["idx"] = None, -1
            _rebuild(); _fill(); refresh_preview()

        def edit_sel():
            s = lb.curselection()
            if not s: self.status_text.set("请先选择"); return
            it = items[s[0]]
            ew = tk.Toplevel(review_win)
            ew.title("编辑闪现点"); ew.transient(review_win); ew.grab_set(); ew.resizable(False, False)
            if it["source"] == "new": fx, fy, tx, ty, *_ = it["data"]
            else: frm, to = it["data"]["from"], it["data"]["to"]; fx, fy, tx, ty = frm["x"], frm["y"], to["x"], to["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="起始点", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            fx_v = tk.StringVar(value=str(fx)); fy_v = tk.StringVar(value=str(fy))
            tx_v = tk.StringVar(value=str(tx)); ty_v = tk.StringVar(value=str(ty))
            for r, lbl, var in [(1, "X:", fx_v), (2, "Y:", fy_v)]:
                tk.Label(f, text=lbl, font=("Microsoft YaHei", 9)).grid(row=r, column=0, sticky="e", padx=(0, 4))
                tk.Entry(f, textvariable=var, width=6, font=("Consolas", 10)).grid(row=r, column=1)
            tk.Label(f, text="终点", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            for r, lbl, var in [(4, "X:", tx_v), (5, "Y:", ty_v)]:
                tk.Label(f, text=lbl, font=("Microsoft YaHei", 9)).grid(row=r, column=0, sticky="e", padx=(0, 4))
                tk.Entry(f, textvariable=var, width=6, font=("Consolas", 10)).grid(row=r, column=1)
            tp_var = tk.StringVar(value="one_way")
            tp_row = tk.Frame(f); tp_row.grid(row=6, column=0, columnspan=2, pady=(8, 0))
            if it["source"] == "old": tp_var.set(it["data"].get("type", "one_way"))
            tk.Label(tp_row, text="类型:", font=("Microsoft YaHei", 9)).pack(side="left")
            tk.Radiobutton(tp_row, text="单向", variable=tp_var, value="one_way", font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
            tk.Radiobutton(tp_row, text="双向", variable=tp_var, value="two_way", font=("Microsoft YaHei", 9)).pack(side="left", padx=2)

            def _ap():
                try: nf = (int(fx_v.get()), int(fy_v.get()), int(tx_v.get()), int(ty_v.get()))
                except ValueError: self.status_text.set("坐标必须为整数"); return
                if it["source"] == "new":
                    old_dr = it["data"][4] if len(it["data"]) > 4 else "?"
                    new_flash[it["idx"]] = [nf[0], nf[1], nf[2], nf[3], old_dr, tp_var.get()]
                else:
                    old_flash[it["idx"]] = {
                        "from": {"x": nf[0], "y": nf[1]},
                        "to": {"x": nf[2], "y": nf[3]},
                        "type": tp_var.get()}
                _rebuild(); _fill(); refresh_preview(); ew.destroy()

            bf2 = tk.Frame(f); bf2.grid(row=7, column=0, columnspan=2, pady=(12, 0))
            tk.Button(bf2, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_ap).pack(side="left", padx=4)
            tk.Button(bf2, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

        def save_and_close():
            all_s = []
            for r in new_flash:
                tp = r[5] if len(r) > 5 else "one_way"
                all_s.append({"from": {"x": r[0], "y": r[1]}, "to": {"x": r[2], "y": r[3]}, "type": tp})
            all_s.extend(old_flash)
            self._flash_save(map_name, all_s)
            review_win.destroy()

        tk.Button(bf, text="删除", font=("Microsoft YaHei", 10), width=8, bg="#e74c3c", fg="white", cursor="hand2", command=del_sel).pack(side="left", padx=3)
        tk.Button(bf, text="编辑坐标", font=("Microsoft YaHei", 10), width=8, bg="#3498db", fg="white", cursor="hand2", command=edit_sel).pack(side="left", padx=3)
        tk.Button(bf, text="保存", font=("Microsoft YaHei", 10, "bold"), width=8, bg="#4ecdc4", fg="white", cursor="hand2", command=save_and_close).pack(side="left", padx=3)
        tk.Button(bf, text="取消", font=("Microsoft YaHei", 10), width=8, cursor="hand2", command=review_win.destroy).pack(side="left", padx=3)
        self.root.wait_window(review_win)

    def _flash_save(self, map_name, flashes):
        if not map_name: return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        existing = data.get(map_name, {})
        existing["flash_points"] = flashes
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        for k in ("platforms", "ropes", "jumps"):
            if k not in existing: existing[k] = []
        data[map_name] = existing
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_text.set(f" {len(flashes)}个闪现点已保存至 {MAPS_FILE.name}")
