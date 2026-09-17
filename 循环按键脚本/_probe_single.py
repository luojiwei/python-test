"""临时诊断：重复启动时应只有一个实例，第二个退出并把第一个窗口切到前台。"""
import ctypes
import subprocess
import time
from pathlib import Path

APP = Path(r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\循环按键脚本")
PYW = r"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\pythonw.exe"
PY = r"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe"
LOG = APP / "run.log"

u = ctypes.windll.user32


def foreground_title():
    hwnd = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def main():
    mark = len(LOG.read_text(encoding="utf-8", errors="replace"))
    a = subprocess.Popen([PYW, str(APP / "main.py")], cwd=str(APP))
    print("实例 A PID:", a.pid)
    time.sleep(3.5)

    t0 = time.monotonic()
    b = subprocess.run([PY, str(APP / "main.py")], cwd=str(APP), capture_output=True,
                       encoding="utf-8", errors="replace", timeout=30)
    cost = time.monotonic() - t0
    print("实例 B 退出码:", b.returncode, "耗时 %.2f 秒" % cost)
    print("实例 B 输出:", repr(b.stdout.strip()), repr(b.stderr.strip()))
    time.sleep(0.5)
    print("当前前台窗口:", foreground_title())

    print("---- 新增日志 ----")
    for ln in LOG.read_text(encoding="utf-8", errors="replace")[mark:].splitlines():
        print(ln)

    k = ctypes.windll.kernel32
    h = k.OpenProcess(1, False, a.pid)
    k.TerminateProcess(h, 0)
    k.CloseHandle(h)
    print("已结束实例 A PID:", a.pid)


main()
