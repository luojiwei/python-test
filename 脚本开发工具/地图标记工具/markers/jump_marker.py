"""JumpMixin — 标记 Mixin 类。"""



import ctypes, json, os, threading, time, tkinter as tk
import mss
import numpy as np
from PIL import Image, ImageTk

try:
    from .config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR
    from .drawing import draw_jump_preview
    from .player_detection import PlayerTracker, detect_player_dot
except ImportError:
    from config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR  # type: ignore[no-redef]
    from drawing import draw_jump_preview  # type: ignore[no-redef]
    from player_detection import PlayerTracker, detect_player_dot  # type: ignore[no-redef]


class JumpMixin:
    # ==================== 3. Jump marking ====================

    def _on_jump_toggle(self) -> None:
        if self._mode == "platform":
            self.status_text.set("平台标记运行中，请先停止"); return
        if self._mode == "rope":
            self.status_text.set("绳梯标记运行中，请先停止"); return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止"); return
        if self._mode == "jump":
            self._jump_stop()
        else:
            self._jump_start()

    def _jump_start(self) -> None:
        if not self._check_minimap_ready(): return
        map_name: str = self.map_name_var.get().strip()
        ml: int = int(self.mm_left_var.get())
        mt: int = int(self.mm_top_var.get())
        mr: int = int(self.mm_right_var.get())
        mb: int = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb)
        self.mm_size = (mr - ml, mb - mt)
        self.status_text.set(f"跳跃点标记中... (地图: {map_name})")
        self.jump_detector.reset()
        self.player_tracker = PlayerTracker()
        self._mm_snapshot = None
        self.frame_count = 0
        self.running = True
        self._mode = "jump"
        self._jump_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="jump")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(target=self._loop_jump, args=(map_name,), daemon=True)
        self.thread.start()

    def _jump_stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._jump_button_set_running(False)
        self._set_mode_buttons("normal")
        self.confirm_btn.config(state="normal")
        self._mode = None
        if self.jump_detector.count > 0:
            self._jump_review_and_save()
        else:
            self.status_text.set("跳跃点标记已停止 | 未检测到跳跃")

    def _jump_button_set_running(self, running: bool) -> None:
        btn = self.mode_buttons["jump"]
        if running:
            btn.config(text="停止跳跃点标记", bg="#ff6b6b", activebackground="#e85a5a")
        else:
            btn.config(text="跳跃点标记", bg="#1abc9c", activebackground="#16a085")

    def _loop_jump(self, map_name: str) -> None:
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
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]
                if self._mm_snapshot is None:
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                pos = detect_player_dot(mm, self.player_tracker)
                if pos is not None: self.jump_detector.add(pos[0], pos[1])
                self.frame_count += 1
                now: float = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"跳跃点标记中... {self.frame_count}帧 | "
                        f"已检测{self.jump_detector.count}次跳跃")
                    last_status = now
            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}"); break
            sleep_t: float = interval - (time.time() - t0)
            if sleep_t > 0: time.sleep(sleep_t)
        msg: str = f"已停止  {self.frame_count}帧 | 检测到{self.jump_detector.count}次跳跃"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Jump Review & Save ====================

    def _jump_review_and_save(self) -> None:
        try: self._jump_review_and_save_impl()
        except Exception as e:
            import traceback
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_jump_error.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.status_text.set(f"跳跃审阅窗口错误: {e}")

    def _jump_review_and_save_impl(self) -> None:
        map_name: str = self.map_name_var.get().strip()
        yoff: int = self.jump_detector.y_offset
        new_jumps: list = [[fx, fy + yoff, tx, ty + yoff]
                           for fx, fy, tx, ty in self.jump_detector.jumps]
        old_jumps: list = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
            old_jumps = data.get(map_name, {}).get("jumps", [])
            if not old_jumps:
                old_jumps = data.get(map_name, {}).get("teleports", [])

        sw, sh = self.mm_size
        scale: float = min(6.0, 700 / max(sw, sh, 1))
        dw: int = int(sw * scale); dh: int = int(sh * scale)

        state: dict = {"source": None, "idx": -1}

        rope_items_nj: list = []
        rope_labels_nj: list[str] = []

        def _rebuild() -> None:
            nonlocal rope_items_nj, rope_labels_nj
            rope_items_nj, rope_labels_nj = [], []
            for i, r in enumerate(new_jumps):
                fx, fy, tx, ty = r
                rope_items_nj.append({"source": "new", "idx": i, "data": r})
                rope_labels_nj.append(f"新{i + 1}: ({fx},{fy}) -> ({tx},{ty})")
            for i, r in enumerate(old_jumps):
                frm, to = r["from"], r["to"]
                rope_items_nj.append({"source": "old", "idx": i, "data": r})
                rope_labels_nj.append(f"旧{i + 1}: ({frm['x']},{frm['y']}) -> ({to['x']},{to['y']})")

        _rebuild()

        def draw_preview() -> ImageTk.PhotoImage:
            img: Image.Image = draw_jump_preview(
                self._mm_snapshot, self.mm_size, map_name,
                new_jumps, old_jumps, state["source"], state["idx"],
                target_size=(dw, dh))
            return ImageTk.PhotoImage(img)

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅跳跃点 - {map_name}")
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
        tk.Label(right_frame, text=f"跳跃点: {len(rope_items_nj)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))
        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(list_frame, font=("Consolas", 9), width=32,
                             height=14, selectmode="single", exportselection=False)
        listbox.pack(side="left", fill="both", expand=True)
        sb2 = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        sb2.pack(side="right", fill="y"); listbox.configure(yscrollcommand=sb2.set)

        def _fill_listbox() -> None:
            listbox.delete(0, "end")
            for label in rope_labels_nj:
                listbox.insert("end", label)
        _fill_listbox()

        def on_select(_event=None) -> None:
            sel = listbox.curselection()
            if sel and sel[0] < len(rope_items_nj):
                it = rope_items_nj[sel[0]]
                state["source"], state["idx"] = it["source"], it["idx"]
            else:
                state["source"], state["idx"] = None, -1
            refresh_preview()
        listbox.bind("<<ListboxSelect>>", on_select)

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=6)
        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=4)

        def delete_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先选择"); return
            idx: int = sel[0]
            if idx >= len(rope_items_nj): return
            it = rope_items_nj[idx]
            if it["source"] == "new": new_jumps.pop(it["idx"])
            else: old_jumps.pop(it["idx"])
            state["source"], state["idx"] = None, -1
            _rebuild(); _fill_listbox(); refresh_preview()

        def edit_selected() -> None:
            sel = listbox.curselection()
            if not sel: self.status_text.set("请先选择"); return
            idx: int = sel[0]
            if idx >= len(rope_items_nj): return
            it = rope_items_nj[idx]
            ew = tk.Toplevel(review_win)
            ew.title("编辑跳跃点"); ew.transient(review_win); ew.grab_set()
            ew.resizable(False, False)
            if it["source"] == "new": fx, fy, tx, ty = it["data"]
            else: frm, to = it["data"]["from"], it["data"]["to"]; fx, fy, tx, ty = frm["x"], frm["y"], to["x"], to["y"]
            f = tk.Frame(ew, padx=15, pady=12); f.pack()
            tk.Label(f, text="起跳点", font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="e", padx=(0, 4))
            fx_var = tk.StringVar(value=str(fx)); tk.Entry(f, textvariable=fx_var, width=6, font=("Consolas", 10)).grid(row=1, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="e", padx=(0, 4))
            fy_var = tk.StringVar(value=str(fy)); tk.Entry(f, textvariable=fy_var, width=6, font=("Consolas", 10)).grid(row=2, column=1)
            tk.Label(f, text="落脚点", font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(12, 4))
            tk.Label(f, text="X:", font=("Microsoft YaHei", 9)).grid(row=4, column=0, sticky="e", padx=(0, 4))
            tx_var = tk.StringVar(value=str(tx)); tk.Entry(f, textvariable=tx_var, width=6, font=("Consolas", 10)).grid(row=4, column=1)
            tk.Label(f, text="Y:", font=("Microsoft YaHei", 9)).grid(row=5, column=0, sticky="e", padx=(0, 4))
            ty_var = tk.StringVar(value=str(ty)); tk.Entry(f, textvariable=ty_var, width=6, font=("Consolas", 10)).grid(row=5, column=1)

            def _apply() -> None:
                try: nfx, nfy, ntx, nty = int(fx_var.get()), int(fy_var.get()), int(tx_var.get()), int(ty_var.get())
                except ValueError: self.status_text.set("坐标必须为整数"); return
                if it["source"] == "new": new_jumps[it["idx"]] = [nfx, nfy, ntx, nty]
                else: old_jumps[it["idx"]] = {"from": {"x": nfx, "y": nfy}, "to": {"x": ntx, "y": nty}}
                _rebuild(); _fill_listbox(); refresh_preview(); ew.destroy()

            bf = tk.Frame(f); bf.grid(row=6, column=0, columnspan=2, pady=(12, 0))
            tk.Button(bf, text="确定", font=("Microsoft YaHei", 9, "bold"), width=6, bg="#4ecdc4", fg="white", command=_apply).pack(side="left", padx=4)
            tk.Button(bf, text="取消", font=("Microsoft YaHei", 9), width=6, command=ew.destroy).pack(side="left", padx=4)

        def save_and_close() -> None:
            all_saved: list = []
            for r in new_jumps:
                fx, fy, tx, ty = r
                all_saved.append({"from": {"x": fx, "y": fy}, "to": {"x": tx, "y": ty}})
            all_saved.extend(old_jumps)
            self._jump_save(map_name, all_saved)
            review_win.destroy()

        tk.Button(btn_frame, text="删除", font=("Microsoft YaHei", 10), width=8, bg="#e74c3c", fg="white", cursor="hand2", command=delete_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="编辑坐标", font=("Microsoft YaHei", 10), width=8, bg="#3498db", fg="white", cursor="hand2", command=edit_selected).pack(side="left", padx=3)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"), width=8, bg="#4ecdc4", fg="white", cursor="hand2", command=save_and_close).pack(side="left", padx=3)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10), width=8, cursor="hand2", command=review_win.destroy).pack(side="left", padx=3)
        self.root.wait_window(review_win)

    def _jump_save(self, map_name: str, jumps: list) -> None:
        if not map_name: return
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        else: data = {}
        existing: dict = data.get(map_name, {})
        existing["jumps"] = jumps
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "platforms" not in existing: existing["platforms"] = []
        if "ropes" not in existing: existing["ropes"] = []
        data[map_name] = existing
        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_text.set(f" {len(jumps)}个跳跃点已保存至 {MAPS_FILE.name}")
