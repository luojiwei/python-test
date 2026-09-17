"""临时诊断：高频率轮询键盘状态，记录脚本实际发出的按键序列（Ctrl 次数 / 与方向+空格的关系）。"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

APP = Path(r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\循环按键脚本")
PYW = r"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\pythonw.exe"

WATCH = {0x11: "Ctrl", 0x20: "Space", 0x25: "Left", 0x27: "Right"}
u = ctypes.windll.user32
u.GetAsyncKeyState.restype = ctypes.c_short
u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_void_p]
ctypes.windll.winmm.timeBeginPeriod(1)


def find_window(pid):
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits = []

    def cb(hwnd, _):
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            hits.append((hwnd, buf.value))
        return True

    u.EnumWindows(callback_type(cb), 0)
    for hwnd, title in hits:
        if title == "循环按键脚本":
            return hwnd
    return None


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    proc = subprocess.Popen([PYW, str(APP / "main.py")], cwd=str(APP))
    print("已启动 PID:", proc.pid)
    time.sleep(3.5)
    hwnd = find_window(proc.pid)
    u.ShowWindow(hwnd, 9)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    u.keybd_event(0x7B, 0, 0, 0)      # F12 开始
    time.sleep(0.08)
    u.keybd_event(0x7B, 0, 2, 0)
    print("已注入 F12，开始采样 %.0f 秒..." % seconds)

    state = {vk: False for vk in WATCH}
    t0 = time.monotonic()
    events = []
    while time.monotonic() - t0 < seconds:
        now = time.monotonic() - t0
        for vk, name in WATCH.items():
            cur = bool(u.GetAsyncKeyState(vk) & 0x8000)
            if cur != state[vk]:
                events.append((now, name, cur))
                state[vk] = cur
        time.sleep(0.001)

    print("\n按键事件（相对首次采样）:")
    last_round_ctrl = []
    for i, (t, name, down) in enumerate(events):
        gap = "" if i == 0 else "  (+%.3f)" % (t - events[i - 1][0])
        print("  %7.3fs  %-5s %-4s%s" % (t, name, "按下" if down else "松开", gap))

    # 逐轮统计：以「方向键按下」为分界，数它前面有几个 Ctrl
    print("\n每轮 Ctrl 次数统计（以方向键按下切分）:")
    counts = []
    pending = 0
    for t, name, down in events:
        if name == "Ctrl" and down:
            pending += 1
        elif name == "Right" and down:
            counts.append(pending)
            pending = 0
    print("  各轮 Ctrl 次数:", counts if counts else "未捕捉到方向键")

    k = ctypes.windll.kernel32
    h = k.OpenProcess(1, False, proc.pid)
    k.TerminateProcess(h, 0)
    k.CloseHandle(h)
    print("已结束 PID:", proc.pid)


main()
