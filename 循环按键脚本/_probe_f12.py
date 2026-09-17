"""临时诊断：把焦点切到脚本窗口后注入热键，验证启停/暂停/退出与日志。"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

APP = Path(r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\循环按键脚本")
PYW = r"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\pythonw.exe"
LOG = APP / "run.log"

VK = {"F8": 0x77, "F9": 0x78, "F12": 0x7B}

u = ctypes.windll.user32
u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_void_p]


def press(name):
    vk = VK[name]
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.08)
    u.keybd_event(vk, 0, 2, 0)
    print(">>> 注入 %s" % name)


def focus_window(pid):
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits = []

    def cb(hwnd, _):
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip():
                hits.append((hwnd, buf.value))
        return True

    u.EnumWindows(callback_type(cb), 0)
    if not hits:
        return None
    hwnd, title = hits[0]
    u.ShowWindow(hwnd, 9)
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)
    return title


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"
    mark = len(LOG.read_text(encoding="utf-8", errors="replace"))
    proc = subprocess.Popen([PYW, str(APP / "main.py")], cwd=str(APP))
    print("已启动 PID:", proc.pid, "模式:", mode)
    time.sleep(3.5)
    print("前台窗口:", focus_window(proc.pid))
    time.sleep(1.0)

    if mode == "single":
        press("F12")
        time.sleep(7.0)
    elif mode == "rapid":
        press("F12")
        time.sleep(0.1)
        press("F12")
        time.sleep(7.0)
    elif mode == "double":
        press("F12")
        time.sleep(5.0)
        press("F12")
        time.sleep(3.0)
    elif mode == "pause":
        press("F12")
        time.sleep(5.0)
        press("F8")
        time.sleep(2.0)
        press("F8")
        time.sleep(1.5)
        press("F12")
        time.sleep(2.0)
    elif mode == "quit":
        press("F12")
        time.sleep(2.0)
        press("F9")
        time.sleep(2.0)
        print("进程是否已退出:", proc.poll() is not None)

    print("---- 新增日志 ----")
    for ln in LOG.read_text(encoding="utf-8", errors="replace")[mark:].splitlines():
        print(ln)

    if proc.poll() is None:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(1, False, proc.pid)
        k.TerminateProcess(h, 0)
        k.CloseHandle(h)
        print("已结束 PID:", proc.pid)
    else:
        print("进程已自行退出，退出码:", proc.poll())


main()
