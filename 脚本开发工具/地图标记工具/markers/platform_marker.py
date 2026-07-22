"""PlatformMixin — 标记 Mixin 类。"""



import ctypes, json, os, threading, time, tkinter as tk
import cv2, mss
import numpy as np
from PIL import Image, ImageTk

try:
    from .config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR
    from .drawing import draw_platform_preview
    from .player_detection import detect_player_dot
    from .rdp_simplify import rdp_simplify
except ImportError:
    from config import CAPTURE_FPS, MAPS_FILE, OUTPUT_DIR  # type: ignore[no-redef]
    from drawing import draw_platform_preview  # type: ignore[no-redef]
    from player_detection import detect_player_dot  # type: ignore[no-redef]
    from rdp_simplify import rdp_simplify  # type: ignore[no-redef]


class PlatformMixin:
    # ==================== 2. Platform marking ====================

    def _on_platform_toggle(self):
        if self._mode == "rope":
            self.status_text.set("绳梯标记运行中，请先停止")
            return
        if self._mode == "jump":
            self.status_text.set("跳跃点标记运行中，请先停止")
            return
        if self._mode == "flash":
            self.status_text.set("闪现点标记运行中，请先停止")
            return
        if self._mode == "platform":
            self._platform_stop()
        else:
            self._platform_start()

    def _platform_start(self):
        if not self._check_minimap_ready():
            return
        map_name = self.map_name_var.get().strip()

        ml = int(self.mm_left_var.get())
        mt = int(self.mm_top_var.get())
        mr = int(self.mm_right_var.get())
        mb = int(self.mm_bottom_var.get())
        self.mm_offsets = (ml, mt, mr, mb)
        self.mm_size = (mr - ml, mb - mt)

        self.status_text.set(f"平台标记中... 记录角色位置 (地图: {map_name})")

        self.platform_recorder.reset()
        self.frame_count = 0
        self.running = True
        self._mode = "platform"
        self._platform_button_set_running(True)
        self._set_mode_buttons("disabled", except_key="platform")
        self.confirm_btn.config(state="disabled")
        self.thread = threading.Thread(
            target=self._loop_platform, args=(map_name,), daemon=True)
        self.thread.start()

    def _platform_stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self._platform_button_set_running(False)
        self._set_mode_buttons("normal")
        self.confirm_btn.config(state="normal")
        self._mode = None

        if self.platform_recorder.count > 0:
            self._platform_review_and_save()
        else:
            self.status_text.set("平台标记已停止 | 未记录到任何位置")

    def _platform_button_set_running(self, running):
        btn = self.mode_buttons["platform"]
        if running:
            btn.config(text="停止平台标记", bg="#ff6b6b", activebackground="#e85a5a")
        else:
            btn.config(text="平台标记", bg="#9b59b6", activebackground="#8e44ad")

    def _loop_platform(self, map_name):
        import ctypes
        sct = mss.MSS()
        interval = 1.0 / CAPTURE_FPS
        debug_frame_saved = False
        last_status = time.time()

        while self.running:
            t0 = time.time()
            try:
                r = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(self.target_hwnd, ctypes.byref(r))
                gl, gt, gr, gb = r.left, r.top, r.right, r.bottom
                if gr <= gl or gb <= gt:
                    time.sleep(0.1)
                    continue

                ml, mt, mr, mb = self.mm_offsets
                ml_abs = gl + ml
                mt_abs = gt + mt
                mw = mr - ml
                mh = mb - mt

                if mw <= 0 or mh <= 0:
                    time.sleep(0.1)
                    continue

                region = {"left": ml_abs, "top": mt_abs,
                          "width": mw, "height": mh}
                img_raw = sct.grab(region)
                mm = np.array(img_raw)[:, :, :3]

                if not debug_frame_saved:
                    os.makedirs(OUTPUT_DIR, exist_ok=True)
                    cv2.imwrite(str(OUTPUT_DIR / "debug_platform_mm.png"), mm)
                    self._mm_snapshot = Image.fromarray(mm[:, :, ::-1])
                    debug_frame_saved = True

                pos = detect_player_dot(mm, self.player_tracker)
                if pos:
                    self.platform_recorder.add(pos[0], pos[1])

                self.frame_count += 1

                now = time.time()
                if now - last_status > 0.5:
                    self.root.after(0, self.status_text.set,
                        f"平台标记中... {self.frame_count}帧 | "
                        f"记录{self.platform_recorder.count}个位置")
                    last_status = now

            except Exception as e:
                self.root.after(0, self.status_text.set, f"错误: {e}")
                break

            sleep_t = interval - (time.time() - t0)
            if sleep_t > 0:
                time.sleep(sleep_t)

        msg = f"已停止  {self.frame_count}帧 | 记录{self.platform_recorder.count}个位置"
        self.root.after(0, self.status_text.set, msg)

    # ==================== Platform Review & Save ====================

    def _platform_review_and_save(self):
        try:
            self._platform_review_and_save_impl()
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(OUTPUT_DIR / "_platform_error.log", "w", encoding="utf-8") as f:
                f.write(err)
            self.status_text.set(f"审阅窗口错误: {e}")

    def _platform_review_and_save_impl(self):
        map_name = self.map_name_var.get().strip()
        positions = self.platform_recorder.get_positions()
        if not positions:
            self.status_text.set("无位置数据, 跳过保存")
            return

        existing_platforms = []
        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing_platforms = data.get(map_name, {}).get("platforms", [])

        sw, sh = self.mm_size
        scale = min(6.0, 700 / max(sw, sh, 1))
        dw = int(sw * scale)
        dh = int(sh * scale)

        state = {
            "selected_platform_idx": 0,
            "preview_img": None,
            "delete_list": [],
        }

        check_vars = [tk.BooleanVar(value=True) for _ in range(len(positions))]

        def get_active_positions():
            return [positions[i] for i, v in enumerate(check_vars) if v.get()]

        def get_preview_positions_and_active():
            active = get_active_positions()
            active_set = set(active)
            all_pts = list(positions)
            if state["selected_platform_idx"] > 0:
                idx = state["selected_platform_idx"] - 1
                if idx < len(existing_platforms):
                    ep_pts = existing_platforms[idx].get("all_points", [])
                    all_pts.extend(ep_pts)
            return all_pts, active_set

        def draw_preview():
            all_pts, active_set = get_preview_positions_and_active()
            if not all_pts:
                all_pts = list(positions)
                active_set = set(positions)
            img = draw_platform_preview(self._mm_snapshot, self.mm_size,
                map_name, all_pts, target_size=(dw, dh), active_set=active_set)
            state["preview_img"] = ImageTk.PhotoImage(img)
            return state["preview_img"]

        review_win = tk.Toplevel(self.root)
        review_win.title(f"审阅平台 - {map_name}")
        review_win.transient(self.root)
        review_win.grab_set()

        img_frame = tk.Frame(review_win)
        img_frame.pack(side="left", padx=10, pady=10)
        canvas = tk.Canvas(img_frame, width=dw, height=dh, highlightthickness=0)
        canvas.pack()
        initial_img = draw_preview()
        canvas.create_image(0, 0, anchor="nw", image=initial_img)
        canvas.photo = initial_img

        def refresh_preview():
            new_img = draw_preview()
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=new_img)
            canvas.photo = new_img

        right_frame = tk.Frame(review_win)
        right_frame.pack(side="right", padx=10, pady=10, fill="both", expand=True)

        tk.Label(right_frame, text=f"记录位置: {len(positions)} 个",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 5))

        list_frame = tk.Frame(right_frame)
        list_frame.pack(fill="both", expand=True)
        inner = tk.Frame(list_frame)
        inner.pack(fill="both", expand=True)

        canvas_list = tk.Canvas(inner, width=280, height=200, highlightthickness=0)
        scrollbar = tk.Scrollbar(inner, orient="vertical", command=canvas_list.yview)
        pos_frame = tk.Frame(canvas_list)

        canvas_list.create_window((0, 0), window=pos_frame, anchor="nw")
        canvas_list.configure(yscrollcommand=scrollbar.set)

        def _on_frame_configure(event=None):
            canvas_list.configure(scrollregion=canvas_list.bbox("all"))
        pos_frame.bind("<Configure>", _on_frame_configure)

        canvas_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def refresh_position_list():
            for widget in pos_frame.winfo_children():
                widget.destroy()
            for i, (x, y) in enumerate(positions):
                var = check_vars[i]
                row = tk.Frame(pos_frame)
                row.pack(fill="x", pady=1)
                chk = tk.Checkbutton(row, variable=var,
                    font=("Consolas", 9), text=f"P{i+1:03d}: ({x:>3}, {y:>3})",
                    command=refresh_preview)
                chk.pack(side="left", padx=2)
                btn = tk.Button(row, text="×", font=("Microsoft YaHei", 8, "bold"),
                    fg="#e74c3c", width=2, command=lambda idx=i: _remove_position(idx))
                btn.pack(side="right", padx=2)

        def _remove_position(idx):
            nonlocal positions
            positions.pop(idx)
            check_vars.pop(idx)
            refresh_position_list()
            refresh_preview()

        refresh_position_list()

        tk.Frame(right_frame, height=1, bg="#ccc").pack(fill="x", pady=8)

        tk.Label(right_frame, text="保存到:", font=("Microsoft YaHei", 9, "bold")
                 ).pack(anchor="w")
        radio_var = tk.IntVar(value=0)

        radio_frame = tk.Frame(right_frame)
        radio_frame.pack(anchor="w", pady=4)
        tk.Radiobutton(radio_frame, text="新平台",
            variable=radio_var, value=0, font=("Microsoft YaHei", 9),
            command=lambda: _on_radio_change(0)).pack(anchor="w")

        for i, plat in enumerate(existing_platforms):
            ep = plat.get("left_endpoint", {})
            pcount = len(plat.get("all_points", []))
            label = f"平台{i+1}: ({ep.get('x','?')},{ep.get('y','?')}) {pcount}点"
            tk.Radiobutton(radio_frame, text=label,
                variable=radio_var, value=i + 1, font=("Microsoft YaHei", 9),
                command=lambda idx=i+1: _on_radio_change(idx)).pack(anchor="w")

        def _on_radio_change(idx):
            state["selected_platform_idx"] = idx
            refresh_preview()

        btn_frame = tk.Frame(right_frame)
        btn_frame.pack(side="bottom", fill="x", pady=10)
        tk.Button(btn_frame, text="保存", font=("Microsoft YaHei", 10, "bold"),
                  width=10, bg="#4ecdc4", fg="white",
                  command=lambda: _save_and_close()).pack(side="left", padx=5)
        tk.Button(btn_frame, text="取消", font=("Microsoft YaHei", 10),
                  width=8, command=review_win.destroy).pack(side="left", padx=5)

        def _save_and_close():
            active = get_active_positions()
            if not active:
                self.status_text.set("没有选中的位置, 跳过保存")
                review_win.destroy()
                return

            if state["selected_platform_idx"] > 0:
                idx = state["selected_platform_idx"] - 1
                if idx < len(existing_platforms):
                    merged = list(existing_platforms[idx].get("all_points", []))
                else:
                    merged = []
                merged.extend(active)
                all_pts = merged
            else:
                all_pts = list(active)

            all_pts.sort(key=lambda p: (p[0], p[1]))
            unique = []
            for p in all_pts:
                if not unique or p != unique[-1]:
                    unique.append(p)
            all_pts = unique

            platform_data = self._compute_platform_data(all_pts)

            if state["selected_platform_idx"] > 0:
                idx = state["selected_platform_idx"] - 1
                existing_platforms[idx] = platform_data
                self._platform_save(map_name, existing_platforms)
            else:
                existing_platforms.append(platform_data)
                self._platform_save(map_name, existing_platforms)

            review_win.destroy()

        self.root.wait_window(review_win)

    def _compute_platform_data(self, all_points):
        adjusted = [(x, y + self.PLATFORM_Y_OFFSET) for x, y in all_points]
        adjusted.sort(key=lambda p: p[0])

        simplified = rdp_simplify(adjusted, epsilon=self.PLATFORM_RDP_EPSILON)
        if len(simplified) < 2:
            simplified = [adjusted[0], adjusted[-1]]

        left_ep = {"x": simplified[0][0], "y": simplified[0][1]}
        right_ep = {"x": simplified[-1][0], "y": simplified[-1][1]}

        all_y = [p[1] for p in simplified]
        min_y = min(all_y)
        max_y = max(all_y)
        avg_y = sum(all_y) // len(all_y)

        turning_points = []
        for x, y in simplified:
            turning_points.append({"x": x, "y": y, "type": "valley"})

        return {
            "left_endpoint": left_ep,
            "right_endpoint": right_ep,
            "min_y": min_y,
            "max_y": max_y,
            "avg_y": avg_y,
            "turning_points": turning_points,
            "all_points": adjusted,
        }

    def _platform_save(self, map_name, platforms: list):
        if not map_name or not platforms:
            self.status_text.set("无数据, 跳过保存")
            return
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        if MAPS_FILE.exists():
            with open(MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        existing = data.get(map_name, {})
        existing["platforms"] = platforms
        existing["minimap_size"] = list(self.mm_size)
        existing["mm_region"] = list(self.mm_offsets)
        if "ropes" not in existing: existing["ropes"] = []
        if "jumps" not in existing: existing["jumps"] = []
        if "flash_points" not in existing: existing["flash_points"] = []
        data[map_name] = existing

        with open(MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.status_text.set(
            self.status_text.get() +
            f" | {len(platforms)}个平台已保存至 {MAPS_FILE.name}")

    # ==================== 3. Jump marking ====================
