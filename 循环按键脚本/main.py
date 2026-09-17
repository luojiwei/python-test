"""main.py — 循环按键脚本（独立工具，不依赖 自动脚本开发 / 脚本开发工具）

工作流程
--------
    阶段【右】：反复执行 [Ctrl ×N → 右方向键 + 空格]，持续「单阶段时长」秒
    阶段【左】：反复执行 [Ctrl ×N → 左方向键 + 空格]，持续「单阶段时长」秒
    两阶段交替，无限循环。

    开始（「开场缓冲」秒之后）先依次按一遍 1、2、3、4；此后每「数字键周期」秒再按一遍
    （主键盘字母上方的数字行，不是右侧小键盘）。
    该计时器与阶段循环各自走自己的周期，互不同步。

数字键让位
----------
    数字键到点时，主循环会在「两个动作之间」的安全点主动停下并等候，
    等 1、2、3、4 全部按完再继续，期间主循环不会发出任何按键，
    因此不会出现 Ctrl / 方向键+空格 与数字键相互叠加的情况。

界面与热键
----------
    窗口内可配置全部时间参数，改动实时写入 config_cache.json，下次启动自动恢复。
    F12   开始 / 停止（停止后回到未开始状态，再次按 F12 会重新走开场缓冲与开场按键）
    F8    暂停 / 继续（暂停时所有计时冻结，恢复后接着走）
    F9    退出（退出前自动释放所有按键）

    每次按键都会写日志（「热键 F12 按下」→「开始运行（F12）」），
    并且 0.3 秒内的重复触发会被忽略，避免连按两下把自己关掉。
    开场缓冲期间状态栏显示「开场缓冲 剩余 N 秒」，以此确认脚本确实已经跑起来了。

    同时只允许一个实例：重复双击启动时，新实例直接退出并把已有窗口切到前台，
    避免两个实例同时抢同一个 F12 与前台按键。

注意
----
    按键发送给「当前前台窗口」，开始前请把游戏窗口切到前台。
    按键方式与 自动脚本开发/input_utils.py 保持一致（keybd_event + 扫描码，
    方向键带 EXTENDEDKEY 标志）。

自检
----
    python main.py --selftest    离线跑一遍循环逻辑，不产生真实按键。
    python main.py --uicheck     只创建界面然后立刻销毁，用于验证 GUI 能正常构建。
"""

from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception as _tk_error:      # 缺 tkinter 的 Python 仍可跑 --selftest
    tk = None
    messagebox = ttk = None
    TK_IMPORT_ERROR = repr(_tk_error)
else:
    TK_IMPORT_ERROR = ""

# ============================================================
# 路径
# ============================================================

SCRIPT_DIR: Path = Path(__file__).resolve().parent
LOG_PATH: Path = SCRIPT_DIR / "run.log"
CONFIG_PATH: Path = SCRIPT_DIR / "config_cache.json"

# ============================================================
# 动作定义
# ============================================================

PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("右", ("right", "space")),
    ("左", ("left", "space")),
)
NUMBER_KEYS: tuple[str, ...] = ("1", "2", "3", "4")

# 热键
VK_F8: int = 0x77
VK_F9: int = 0x78
VK_F12: int = 0x7B

HOTKEY_LABEL: dict[int, str] = {VK_F8: "F8", VK_F9: "F9", VK_F12: "F12"}
HOTKEY_DEBOUNCE: float = 0.3     # 同一热键在此秒数内的重复触发视为误触，忽略
MUTEX_NAME: str = "LoopKeyScript_SingleInstance"
WINDOW_TITLE: str = "循环按键脚本"

POLL_INTERVAL: float = 0.02
UI_REFRESH_MS: int = 200
ROUND_LOG_INTERVAL: float = 5.0   # 每轮动作日志的最小间隔（秒），避免刷屏


@dataclass
class Timing:
    """全部时间参数。"""
    start_countdown: float = 3.0     # 开场缓冲（秒）
    phase_duration: float = 120.0    # 单阶段时长（秒）
    ctrl_count: int = 3              # Ctrl 连按次数
    ctrl_hold: float = 0.05          # Ctrl 按住（秒）
    ctrl_gap: float = 0.06           # Ctrl 间隔（秒）
    chord_hold: float = 0.10         # 方向键+空格 同时按住（秒）
    chord_gap: float = 0.10          # 方向键+空格 间隔（秒）
    number_interval: float = 280.0   # 数字键周期（秒）
    number_hold: float = 0.05        # 数字键按住（秒）
    number_gap: float = 0.08         # 数字键之间间隔（秒）


# (字段名, 界面标签, 单位, 解析类型)
TIMING_FIELDS: tuple[tuple[str, str, str, type], ...] = (
    ("start_countdown", "开场缓冲", "秒", float),
    ("phase_duration", "单阶段时长", "秒", float),
    ("ctrl_count", "Ctrl 连按次数", "次", int),
    ("ctrl_hold", "Ctrl 按住", "秒", float),
    ("ctrl_gap", "Ctrl 间隔", "秒", float),
    ("chord_hold", "方向+空格 按住", "秒", float),
    ("chord_gap", "方向+空格 间隔", "秒", float),
    ("number_interval", "数字键周期", "秒", float),
    ("number_hold", "数字键按住", "秒", float),
    ("number_gap", "数字键间隔", "秒", float),
)

# ============================================================
# 按键表：(虚拟键码, 扫描码, 是否扩展键)
# ============================================================

KEY_MAP: dict[str, tuple[int, int, bool]] = {
    "ctrl": (0x11, 0x1D, False),
    "left": (0x25, 0x4B, True),
    "right": (0x27, 0x4D, True),
    "space": (0x20, 0x39, False),
    "1": (0x31, 0x02, False),
    "2": (0x32, 0x03, False),
    "3": (0x33, 0x04, False),
    "4": (0x34, 0x05, False),
}

KEY_LABEL: dict[str, str] = {
    "ctrl": "Ctrl", "left": "左方向", "right": "右方向", "space": "空格",
    "1": "1", "2": "2", "3": "3", "4": "4",
}

KEYEVENTF_EXTENDEDKEY: int = 0x0001
KEYEVENTF_KEYUP: int = 0x0002

# ============================================================
# 日志
# ============================================================

class Logger:
    """日志：追加写入 run.log，同时抛给界面（sink 为空则打印到控制台）。"""

    def __init__(self, path: Path, sink=None) -> None:
        self._path = path
        self._sink = sink
        self._lock = threading.Lock()

    def __call__(self, msg: str) -> None:
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        with self._lock:
            try:
                with open(str(self._path), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        if self._sink is not None:
            self._sink(line)
        else:
            try:
                print(line, flush=True)
            except Exception:
                pass


# ============================================================
# 配置持久化
# ============================================================

def load_config(path: Path) -> dict:
    """读取 config_cache.json，失败返回空 dict。"""
    if not path.exists():
        return {}
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_config(path: Path, values: dict) -> None:
    """写入 config_cache.json，失败不阻塞流程。"""
    try:
        with open(str(path), "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def format_default(value) -> str:
    """把默认值格式化成输入框文本（整数不带小数点）。"""
    if isinstance(value, int):
        return str(value)
    return ("%.3f" % value).rstrip("0").rstrip(".")


# ============================================================
# 按键发送
# ============================================================

class KeySender:
    """keybd_event 按键发送。

    每个动作（tap / chord / sequence）在自己的锁内一次性做完，
    不会出现「按下一半被别的动作插进来」的情况。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @staticmethod
    def _event(key: str, down: bool) -> None:
        vk, scan, extended = KEY_MAP[key]
        flags = KEYEVENTF_EXTENDEDKEY if (extended and down) else 0
        if not down:
            flags |= KEYEVENTF_KEYUP
        ctypes.windll.user32.keybd_event(vk, scan, flags, 0)

    def tap(self, key: str, hold: float) -> None:
        """单键：按下 → 保持 → 松开。"""
        with self._lock:
            self._event(key, True)
            time.sleep(hold)
            self._event(key, False)

    def chord(self, keys: tuple, hold: float) -> None:
        """多键同时按下 → 保持 → 同时松开。"""
        with self._lock:
            for k in keys:
                self._event(k, True)
            time.sleep(hold)
            for k in reversed(keys):
                self._event(k, False)

    def sequence(self, keys: tuple, hold: float, gap: float) -> None:
        """依次敲击一串键，整段期间持锁。"""
        with self._lock:
            for i, k in enumerate(keys):
                self._event(k, True)
                time.sleep(hold)
                self._event(k, False)
                if i < len(keys) - 1:
                    time.sleep(gap)

    def release_all(self) -> None:
        """兜底：把所有用到的键全部抬起。"""
        with self._lock:
            for key in KEY_MAP:
                vk, scan, extended = KEY_MAP[key]
                flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if extended else 0)
                ctypes.windll.user32.keybd_event(vk, scan, flags, 0)


# ============================================================
# 活动时钟（暂停时冻结）
# ============================================================

class Clock:
    """只累计「运行中」的时间：暂停期间时间静止，恢复后接着走。"""

    def __init__(self, pause_event: threading.Event) -> None:
        self._pause = pause_event
        self._lock = threading.Lock()
        self._last = time.monotonic()
        self._elapsed = 0.0
        self._was_paused = pause_event.is_set()

    def now(self) -> float:
        with self._lock:
            t = time.monotonic()
            paused = self._pause.is_set()
            if paused or self._was_paused:
                # 暂停期间（含刚恢复的第一次读取）不计时
                self._last = t
            else:
                self._elapsed += t - self._last
                self._last = t
            self._was_paused = paused
            return self._elapsed

    def sleep(self, duration: float, stop_event: threading.Event) -> bool:
        """可暂停睡眠。返回 False 表示收到停止信号。"""
        target = self.now() + duration
        while True:
            if stop_event.is_set():
                return False
            if self._pause.is_set():
                time.sleep(POLL_INTERVAL)
                continue
            remaining = target - self.now()
            if remaining <= 0:
                return True
            time.sleep(min(POLL_INTERVAL, remaining))

    def wait_resume(self, stop_event: threading.Event) -> bool:
        """暂停中则阻塞等待恢复。返回 False 表示收到停止信号。"""
        while self._pause.is_set():
            if stop_event.is_set():
                return False
            time.sleep(POLL_INTERVAL)
        return not stop_event.is_set()


# ============================================================
# 数字键让位握手
# ============================================================

class NumberGate:
    """主循环与 1/2/3/4 序列之间的让位握手。

    数字键到点 → request 置位；主循环走到动作之间的安全点 → ack 回应并停发按键；
    数字键按完 → 清 request；主循环清 ack 后继续。全程无按键交叠。
    """

    def __init__(self, logger: Logger) -> None:
        self.request = threading.Event()
        self.ack = threading.Event()
        self._logger = logger

    def yield_if_requested(self, stop_event: threading.Event) -> bool:
        """主循环在安全点调用：若有数字键请求让位，则停下等它按完。"""
        if not self.request.is_set():
            return not stop_event.is_set()
        self._logger("让位给数字键 %s，主循环暂停" % " ".join(NUMBER_KEYS))
        self.ack.set()
        while self.request.is_set():
            if stop_event.is_set():
                self.ack.clear()
                return False
            time.sleep(POLL_INTERVAL)
        self.ack.clear()
        self._logger("数字键按完，主循环继续")
        return True

    def acquire(self, stop_event: threading.Event,
                pause_event: threading.Event) -> bool:
        """数字键线程调用：请求让位并等主循环停稳。返回 False 表示需要终止。"""
        self.request.set()
        while not self.ack.is_set():
            if stop_event.is_set():
                self.request.clear()
                return False
            time.sleep(POLL_INTERVAL)
        # 主循环已停在安全点；若此刻处于暂停，则等恢复后再按
        while pause_event.is_set():
            if stop_event.is_set():
                self.release()
                return False
            time.sleep(POLL_INTERVAL)
        return True

    def release(self) -> None:
        self.request.clear()


# ============================================================
# 运行上下文 & 状态
# ============================================================

class RunState:
    """运行状态快照，供界面定时读取（各 *_end 用活动时钟的绝对值）。"""

    def __init__(self) -> None:
        self.phase = "-"
        self.countdown_end = None    # 开场缓冲结束时刻
        self.phase_end = None        # 当前阶段结束时刻
        self.number_next = None      # 下次数字键时刻
        self.cycle = 0


@dataclass
class Ctx:
    """工作线程共享的上下文。"""
    clock: Clock
    timing: Timing
    state: RunState
    keys: KeySender
    gate: NumberGate
    stop: threading.Event
    pause: threading.Event
    started: threading.Event
    logger: Logger


# ============================================================
# 工作线程
# ============================================================

def _run_phase(label: str, chord_keys: tuple, ctx: Ctx) -> bool:
    """跑完一个阶段（固定时长）。返回 False 表示需要终止。"""
    timing = ctx.timing
    chord_text = "+".join(KEY_LABEL[k] for k in chord_keys)
    ctrl_text = "Ctrl×%d（按住 %s / 间隔 %s 秒）" % (
        timing.ctrl_count, format_default(timing.ctrl_hold), format_default(timing.ctrl_gap))
    ctx.logger("阶段【%s】开始：%s → %s，持续 %s 秒"
               % (label, ctrl_text, chord_text, format_default(timing.phase_duration)))

    ctx.state.phase = label
    end = ctx.clock.now() + timing.phase_duration
    ctx.state.phase_end = end
    rounds = 0
    last_round_log = None
    while True:
        if not ctx.clock.wait_resume(ctx.stop):
            return False
        if not ctx.gate.yield_if_requested(ctx.stop):
            return False
        if ctx.clock.now() >= end:
            break

        for _ in range(timing.ctrl_count):
            ctx.keys.tap("ctrl", timing.ctrl_hold)
            if not ctx.clock.sleep(timing.ctrl_gap, ctx.stop):
                return False
            if not ctx.gate.yield_if_requested(ctx.stop):
                return False

        ctx.keys.chord(chord_keys, timing.chord_hold)
        if not ctx.clock.sleep(timing.chord_gap, ctx.stop):
            return False

        rounds += 1
        now = ctx.clock.now()
        if last_round_log is None or now - last_round_log >= ROUND_LOG_INTERVAL:
            ctx.logger("第 %d 轮：%s → %s" % (rounds, ctrl_text, chord_text))
            last_round_log = now

    ctx.state.phase_end = None
    ctx.logger("阶段【%s】结束，共 %d 轮" % (label, rounds))
    return True


def sequence_worker(ctx: Ctx) -> None:
    """主循环：右 / 左 两阶段交替。开跑前先按一遍 1/2/3/4。"""
    ctx.state.countdown_end = ctx.clock.now() + ctx.timing.start_countdown
    if not ctx.clock.sleep(ctx.timing.start_countdown, ctx.stop):
        return
    ctx.state.countdown_end = None

    ctx.logger("开场缓冲结束，先按一遍：%s" % " ".join(NUMBER_KEYS))
    ctx.keys.sequence(NUMBER_KEYS, ctx.timing.number_hold, ctx.timing.number_gap)
    ctx.started.set()

    while not ctx.stop.is_set():
        ctx.state.cycle += 1
        ctx.logger("======== 第 %d 轮循环 ========" % ctx.state.cycle)
        for label, chord_keys in PHASES:
            if not _run_phase(label, chord_keys, ctx):
                return


def number_worker(ctx: Ctx) -> None:
    """数字键计时器：以「开场那一遍」为起点，之后每「数字键周期」秒一遍。"""
    while not ctx.started.is_set():
        if ctx.stop.is_set():
            return
        time.sleep(POLL_INTERVAL)

    next_fire = ctx.clock.now() + ctx.timing.number_interval
    ctx.state.number_next = next_fire
    while not ctx.stop.is_set():
        if not ctx.clock.wait_resume(ctx.stop):
            return
        if ctx.clock.now() >= next_fire:
            ctx.logger("数字键到点：%s（运行时刻 %s 秒）"
                       % (" ".join(NUMBER_KEYS), format_default(round(next_fire))))
            if not ctx.gate.acquire(ctx.stop, ctx.pause):
                return
            ctx.keys.sequence(NUMBER_KEYS, ctx.timing.number_hold, ctx.timing.number_gap)
            ctx.gate.release()
            next_fire += ctx.timing.number_interval
            ctx.state.number_next = next_fire
            continue
        if not ctx.clock.sleep(POLL_INTERVAL, ctx.stop):
            return


def thread_guard(target):
    """包装工作线程：异常写入日志并停止运行，避免 pythonw 下静默死掉。"""
    def wrapper(ctx: Ctx) -> None:
        try:
            target(ctx)
        except Exception:
            ctx.logger("线程 %s 异常：\n%s" % (target.__name__, traceback.format_exc().rstrip()))
            ctx.stop.set()
    return wrapper


# ============================================================
# Windows API
# ============================================================

_user32 = ctypes.windll.user32
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]


def _pressed(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


_MUTEX_HANDLE = None


def _acquire_single_instance() -> bool:
    """命名互斥体保证只有一个实例；已有实例时把它的窗口切到前台并返回 False。"""
    global _MUTEX_HANDLE
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.SetLastError(0)
    _MUTEX_HANDLE = k.CreateMutexW(None, 1, MUTEX_NAME)
    if k.GetLastError() == 183:       # ERROR_ALREADY_EXISTS
        _focus_existing_window()
        return False
    return True


def _focus_existing_window() -> None:
    """把标题为 WINDOW_TITLE 的窗口恢复并切到前台。"""
    hits: list = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lparam):
        length = _user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value == WINDOW_TITLE:
                hits.append(hwnd)
        return True

    _user32.EnumWindows(callback_type(cb), 0)
    if hits:
        _user32.ShowWindow(hits[0], 9)          # SW_RESTORE
        _user32.SetForegroundWindow(hits[0])


# ============================================================
# 界面
# ============================================================

class App:
    def __init__(self, root: tk.Tk, config_path: Path = CONFIG_PATH) -> None:
        self.root = root
        self.config_path = config_path
        self.commands: queue.Queue = queue.Queue()
        self.logger = Logger(LOG_PATH, sink=self._enqueue_log)
        self.vars: dict = {}
        self.keys = KeySender()
        self.state = RunState()

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.started_event = threading.Event()
        self.gate = NumberGate(self.logger)
        self.clock = None
        self.workers: list = []
        self.running = False
        self.quitting = False

        self._build_ui()
        self._load_config()
        self._bind_traces()
        self._start_hotkeys()
        self._refresh_buttons()
        self._poll()

        self.logger("=" * 52)
        self.logger("循环按键脚本已就绪")
        self.logger("F12 = 开始 / 停止      F8 = 暂停 / 继续      F9 = 退出")
        self.logger("提示：按键发送给前台窗口，开始前请把游戏窗口切到前台")
        self.logger("=" * 52)

    # ---------------- 界面构建 ----------------

    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.resizable(False, False)

        param_frame = ttk.LabelFrame(self.root, text="时间参数（可配置，自动缓存）", padding=8)
        param_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))

        defaults = Timing()
        for i, (name, label, unit, _caster) in enumerate(TIMING_FIELDS):
            ttk.Label(param_frame, text=label, width=16, anchor="e").grid(
                row=i, column=0, sticky="e", pady=2)
            var = tk.StringVar(value=format_default(getattr(defaults, name)))
            self.vars[name] = var
            ttk.Entry(param_frame, textvariable=var, width=10, justify="right").grid(
                row=i, column=1, sticky="w", padx=6, pady=2)
            ttk.Label(param_frame, text=unit, width=3).grid(
                row=i, column=2, sticky="w", pady=2)

        status_frame = ttk.LabelFrame(self.root, text="运行状态", padding=8)
        status_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)

        self.status_var = tk.StringVar(value="未开始")
        self.detail_var = tk.StringVar(value="-")
        self.timer_var = tk.StringVar(value="-")
        ttk.Label(status_frame, text="状态：", anchor="e", width=16).grid(row=0, column=0, sticky="e")
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").grid(
            row=0, column=1, sticky="w")
        ttk.Label(status_frame, text="阶段：", anchor="e", width=16).grid(row=1, column=0, sticky="e")
        ttk.Label(status_frame, textvariable=self.detail_var, anchor="w").grid(
            row=1, column=1, sticky="w")
        ttk.Label(status_frame, text="计时：", anchor="e", width=16).grid(row=2, column=0, sticky="e")
        ttk.Label(status_frame, textvariable=self.timer_var, anchor="w").grid(
            row=2, column=1, sticky="w")

        button_frame = ttk.Frame(self.root)
        button_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 6))
        self.start_button = ttk.Button(button_frame, text="开始 (F12)", width=16,
                                       command=self.toggle_script)
        self.start_button.grid(row=0, column=0, padx=2)
        self.pause_button = ttk.Button(button_frame, text="暂停 (F8)", width=14,
                                       command=self.toggle_pause)
        self.pause_button.grid(row=0, column=1, padx=2)
        ttk.Button(button_frame, text="退出 (F9)", width=12, command=self.quit_app).grid(
            row=0, column=2, padx=2)

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=6)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(6, 10))
        self.log_text = tk.Text(log_frame, width=62, height=12, wrap="word",
                                state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

    # ---------------- 配置 ----------------

    def _load_config(self) -> None:
        """恢复缓存的参数；缺失项保持默认值。"""
        cached = load_config(self.config_path)
        defaults = Timing()
        for name, _label, _unit, caster in TIMING_FIELDS:
            if name not in cached:
                continue
            try:
                value = caster(cached[name])
            except (TypeError, ValueError):
                continue
            self.vars[name].set(format_default(value))

    def _save_config(self) -> None:
        values = {}
        defaults = Timing()
        for name, _label, _unit, caster in TIMING_FIELDS:
            raw = self.vars[name].get().strip()
            try:
                values[name] = caster(raw)
            except ValueError:
                values[name] = getattr(defaults, name)
        save_config(self.config_path, values)

    def _bind_traces(self) -> None:
        """任意参数改动即写入缓存。"""
        for name in self.vars:
            self.vars[name].trace_add("write", lambda *_a: self._save_config())

    def collect_timing(self):
        """从输入框读取参数。非法时弹窗提示并返回 None。"""
        values = {}
        for name, label, _unit, caster in TIMING_FIELDS:
            raw = self.vars[name].get().strip()
            try:
                values[name] = caster(raw)
            except ValueError:
                messagebox.showerror("参数错误", "「%s」不是合法数字：%s" % (label, raw))
                return None
        if values["ctrl_count"] < 1:
            messagebox.showerror("参数错误", "「Ctrl 连按次数」至少为 1")
            return None
        for name, label, _unit, caster in TIMING_FIELDS:
            if name != "ctrl_count" and values[name] <= 0:
                messagebox.showerror("参数错误", "「%s」必须大于 0" % label)
                return None
        return Timing(**values)

    # ---------------- 运行控制 ----------------

    def start_script(self, source: str = "按钮") -> None:
        if self.running:
            return
        timing = self.collect_timing()
        if timing is None:
            return
        self._save_config()

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.started_event = threading.Event()
        self.gate = NumberGate(self.logger)
        self.clock = Clock(self.pause_event)
        self.state = RunState()
        ctx = Ctx(clock=self.clock, timing=timing, state=self.state, keys=self.keys,
                  gate=self.gate, stop=self.stop_event, pause=self.pause_event,
                  started=self.started_event, logger=self.logger)

        self.workers = []
        for target in (sequence_worker, number_worker):
            t = threading.Thread(target=thread_guard(target), args=(ctx,),
                                 name=target.__name__, daemon=True)
            t.start()
            self.workers.append(t)

        self.running = True
        self.status_var.set("运行中")
        self.logger("===== 开始运行（%s）=====" % source)
        self.logger("阶段 %s 秒交替，每 %s 秒按一遍 %s"
                    % (format_default(timing.phase_duration),
                       format_default(timing.number_interval), " ".join(NUMBER_KEYS)))
        self.logger("开场缓冲 %s 秒，之后先按一遍 %s，再进入阶段【右】"
                    % (format_default(timing.start_countdown), " ".join(NUMBER_KEYS)))
        self._refresh_buttons()

    def stop_script(self, source: str = "按钮") -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.pause_event.clear()      # 让暂停中的线程立刻醒来退出
        for t in self.workers:
            t.join(timeout=2.0)
        self.keys.release_all()
        self.running = False
        self.status_var.set("已停止")
        self.detail_var.set("-")
        self.timer_var.set("-")
        self.logger("===== 已停止（%s）：本次运行 %s 秒 ====="
                    % (source, format_default(round(self.clock.now() if self.clock else 0))))
        self._refresh_buttons()

    def toggle_script(self, source: str = "按钮") -> None:
        if self.running:
            self.stop_script(source)
        else:
            self.start_script(source)

    def toggle_pause(self, source: str = "按钮") -> None:
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.status_var.set("运行中")
            self.logger("继续运行（%s）" % source)
        else:
            self.pause_event.set()
            self.status_var.set("已暂停")
            self.logger("已暂停（%s），计时冻结" % source)
        self._refresh_buttons()

    def quit_app(self, source: str = "关闭窗口") -> None:
        if self.quitting:
            return
        self.quitting = True
        self._save_config()
        self.stop_script(source)
        self.root.destroy()

    def _refresh_buttons(self) -> None:
        self.start_button.configure(text="停止 (F12)" if self.running else "开始 (F12)")
        state = "normal" if self.running else "disabled"
        self.pause_button.configure(state=state,
                                    text="继续 (F8)" if self.pause_event.is_set() else "暂停 (F8)")

    # ---------------- 线程间通信 ----------------

    def _enqueue_log(self, line: str) -> None:
        self.commands.put(("log", line))

    def _start_hotkeys(self) -> None:
        t = threading.Thread(target=self._hotkey_loop, name="hotkey", daemon=True)
        t.start()

    def _hotkey_loop(self) -> None:
        """轮询 F8/F9/F12：记录每次按下，并丢掉 0.3 秒内的重复触发。"""
        prev = {vk: False for vk in HOTKEY_LABEL}
        last = {vk: 0.0 for vk in HOTKEY_LABEL}
        while not self.quitting:
            for vk, cmd in ((VK_F12, "toggle"), (VK_F8, "pause"), (VK_F9, "quit")):
                cur = _pressed(vk)
                if cur and not prev[vk]:
                    label = HOTKEY_LABEL[vk]
                    gap = time.monotonic() - last[vk]
                    if gap < HOTKEY_DEBOUNCE:
                        self.logger("热键 %s 重复按下（间隔 %.2f 秒），已忽略" % (label, gap))
                    else:
                        last[vk] = time.monotonic()
                        self.logger("热键 %s 按下" % label)
                        self.commands.put((cmd, label))
                prev[vk] = cur
            time.sleep(POLL_INTERVAL)

    def _poll(self) -> None:
        """界面定时任务：处理命令队列 + 刷新状态显示。"""
        try:
            while True:
                cmd, payload = self.commands.get_nowait()
                if cmd == "log":
                    self._append_log(payload)
                elif cmd == "toggle":
                    self.toggle_script(payload or "按钮")
                elif cmd == "pause":
                    self.toggle_pause(payload or "按钮")
                elif cmd == "quit":
                    self.quit_app(payload or "关闭窗口")
                    return
        except queue.Empty:
            pass

        # 工作线程异常退出时自动停止
        if self.running and self.workers and all(not t.is_alive() for t in self.workers):
            self.stop_script("工作线程结束")

        self._refresh_status()
        self.root.after(UI_REFRESH_MS, self._poll)

    def _refresh_status(self) -> None:
        if not self.running or self.clock is None:
            return
        now = self.clock.now()
        if self.state.countdown_end is not None:
            self.detail_var.set("开场缓冲 剩余 %s 秒"
                                % format_default(round(max(0.0, self.state.countdown_end - now))))
        elif self.state.phase_end is not None:
            self.detail_var.set("【%s】剩余 %s 秒"
                                % (self.state.phase, format_default(round(max(0.0, self.state.phase_end - now)))))
        else:
            self.detail_var.set("【%s】阶段切换中" % self.state.phase)
        number_text = "-"
        if self.state.number_next is not None:
            number_text = "距下次数字键 %s 秒" % format_default(
                round(max(0.0, self.state.number_next - now)))
        self.timer_var.set("运行 %s 秒，%s" % (format_default(round(now)), number_text))

    def _append_log(self, line: str) -> None:
        """日志窗口只在滚动条到底时自动跟到底，翻看历史时不跳回。"""
        at_bottom = self.log_text.yview()[1] >= 0.999
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.configure(state="disabled")
        if at_bottom:
            self.log_text.see("end")


# ============================================================
# 主程序
# ============================================================

def _write_log(detail: str, prefix: str = "") -> None:
    """pythonw 下没有控制台，只能靠 run.log 留痕。"""
    try:
        with open(str(LOG_PATH), "a", encoding="utf-8") as f:
            f.write("[%s] %s%s\n" % (datetime.now().strftime("%H:%M:%S"), prefix, detail))
    except OSError:
        pass


def main() -> int:
    if tk is None:
        detail = "tkinter 不可用：%s\n请用带 tkinter 的 Python 启动（如 %s）" % (
            TK_IMPORT_ERROR, r"%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe")
        _write_log(detail, "启动失败：")
        print(detail)
        return 1
    if not _acquire_single_instance():
        _write_log("已有实例在运行，已把它的窗口切到前台；本实例退出。")
        return 0
    root = tk.Tk()
    try:
        App(root)
        root.mainloop()
    except Exception:
        detail = traceback.format_exc()
        _write_log(detail, "未捕获异常：\n")
        try:
            messagebox.showerror("循环按键脚本 崩溃", detail[-1500:])
        except Exception:
            pass
        return 1
    return 0


# ============================================================
# 离线自检（不产生真实按键）
# ============================================================

class MockSender:
    """假发送器：记录每个动作占用的时间区间，用于验证循环逻辑与不交叠。"""

    def __init__(self) -> None:
        self.spans: list = []      # (起, 止, 描述, 是否数字键)
        self._t0 = time.monotonic()

    def _elapsed(self) -> float:
        return time.monotonic() - self._t0

    def _span(self, text: str, work, is_number: bool = False) -> None:
        start = self._elapsed()
        work()
        self.spans.append((start, self._elapsed(), text, is_number))

    def tap(self, key: str, hold: float) -> None:
        self._span(KEY_LABEL[key], lambda: time.sleep(hold))

    def chord(self, keys: tuple, hold: float) -> None:
        self._span("+".join(KEY_LABEL[k] for k in keys), lambda: time.sleep(hold))

    def sequence(self, keys: tuple, hold: float, gap: float) -> None:
        def work() -> None:
            for i in range(len(keys)):
                time.sleep(hold)
                if i < len(keys) - 1:
                    time.sleep(gap)
        self._span("".join(KEY_LABEL[k] for k in keys), work, is_number=True)

    def release_all(self) -> None:
        self._span("release_all", lambda: None)


def _selftest() -> int:
    """用缩短的时长跑一遍完整逻辑，只记录动作区间，不产生真实按键。"""
    logger = Logger(SCRIPT_DIR / "selftest.log")
    timing = Timing(start_countdown=0.1, phase_duration=0.6, ctrl_count=3,
                    ctrl_hold=0.05, ctrl_gap=0.01, chord_hold=0.10, chord_gap=0.01,
                    number_interval=0.6, number_hold=0.02, number_gap=0.02)
    stop_event = threading.Event()
    pause_event = threading.Event()
    started_event = threading.Event()
    clock = Clock(pause_event)
    state = RunState()
    sender = MockSender()
    ctx = Ctx(clock=clock, timing=timing, state=state, keys=sender,
              gate=NumberGate(logger), stop=stop_event, pause=pause_event,
              started=started_event, logger=logger)

    threads = [threading.Thread(target=thread_guard(target), args=(ctx,), daemon=True)
               for target in (sequence_worker, number_worker)]
    for t in threads:
        t.start()
    # 模拟暂停：等在飞的单个动作收尾后打点，暂停期间不应再有任何动作，也不推进计时
    time.sleep(0.5)
    pause_event.set()
    time.sleep(0.15)
    before = len(sender.spans)
    time.sleep(0.4)
    frozen = len(sender.spans) == before
    pause_event.clear()
    time.sleep(2.0)

    stop_event.set()
    for t in threads:
        t.join(timeout=2.0)

    numbers = [s for s in sender.spans if s[3]]
    conflicts = [
        "%6.2f~%6.2f秒 %s ←→ 数字键 %6.2f~%6.2f秒" % (m[0], m[1], m[2], n[0], n[1])
        for n in numbers for m in sender.spans
        if not m[3] and m[0] < n[1] and n[0] < m[1]
    ]
    kinds = [s[2] for s in sender.spans]
    initial_ok = bool(sender.spans) and sender.spans[0][3] and sender.spans[0][2] == "1234"

    # 配置读写往返
    cfg_path = SCRIPT_DIR / "selftest_config.json"
    sample = {"phase_duration": 90.0, "ctrl_count": 4, "number_interval": 200.0}
    save_config(cfg_path, sample)
    cfg_ok = load_config(cfg_path) == sample
    cfg_path.unlink()

    print("\n动作区间:")
    for start, end, text, is_number in sender.spans:
        print("  %6.2f~%6.2f秒  %s%s" % (start, end, text,
                                        "   ← 数字键序列" if is_number else ""))

    print("\n开场先按一遍 1 2 3 4 : %s" % initial_ok)
    print("配置读写往返      : %s" % cfg_ok)
    print("暂停期间无动作    : %s" % frozen)
    print("数字键按压次数    : %d（含开场那一次）" % len(numbers))
    print("与主循环重叠冲突  : %d" % len(conflicts))
    for c in conflicts:
        print("    " + c)

    ok = (initial_ok and cfg_ok and frozen and not conflicts and len(numbers) >= 3
          and "右方向+空格" in kinds and "左方向+空格" in kinds)
    print("自检结果          : %s" % ("通过" if ok else "未通过"))
    return 0 if ok else 1


def _uicheck() -> int:
    """只构建界面并刷新一次，然后销毁，用于验证 GUI 能正常创建。"""
    if tk is None:
        print("tkinter 不可用：%s" % TK_IMPORT_ERROR)
        return 1
    root = tk.Tk()
    app = App(root, config_path=SCRIPT_DIR / "selftest_config.json")
    root.update()
    size = (root.winfo_width(), root.winfo_height())
    print("界面构建成功，窗口尺寸: %s" % (size,))
    print("参数项数量: %d" % len(app.vars))
    app.quitting = True
    root.destroy()
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--uicheck" in sys.argv:
        raise SystemExit(_uicheck())
    raise SystemExit(main())
