"""测试：前置游戏窗口，按 A 键，看游戏有没有反应"""
import ctypes, ctypes.wintypes, time, sys

def enum_windows():
    found = []
    def cb(hwnd, _):
        r = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 300 or h < 200: return True
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        t = buf.value.strip()
        if t: found.append((hwnd, t))
        return True
    WEP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    ctypes.windll.user32.EnumWindows(WEP(cb), 0)
    return found

keyword = sys.argv[1] if len(sys.argv) >= 2 else "冒险岛"
matches = [(h, t) for h, t in enum_windows() if keyword in t]
if not matches: print(f"未找到 '{keyword}'"); exit()
hwnd, title = matches[0]
print(f"窗口: {title} hwnd={hwnd}")

# 前置
ctypes.windll.user32.SetForegroundWindow(hwnd)
time.sleep(0.3)
print("窗口已前置，0.5 秒后按 A...")

time.sleep(0.5)
# 按 A: VK=0x41, SC=0x1E
ctypes.windll.user32.keybd_event(0x41, 0x1E, 0, 0)          # press
time.sleep(0.03)
ctypes.windll.user32.keybd_event(0x41, 0x1E, 0x0002, 0)      # release
print("A 键已发送")
