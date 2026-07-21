"""test_home_key.py — 测试 Home 键是否能通过 keybd_event 发出"""
import ctypes
import time

VK_HOME = 0x24
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# 测试 1：不同扫描码组合
test_cases = [
    ("SC=0x47 + EXTENDEDKEY",   0x47, True),
    ("SC=0x47 无 EXTENDEDKEY",  0x47, False),
    ("SC=0x00 + EXTENDEDKEY",   0x00, True),
    ("SC=0x00 无 EXTENDEDKEY",  0x00, False),
]

print("=== Home 键测试 (VK=0x24) ===\n")

for label, sc, use_ext in test_cases:
    flag_down = KEYEVENTF_EXTENDEDKEY if use_ext else 0

    # 清一下状态
    ctypes.windll.user32.GetAsyncKeyState(VK_HOME)

    # 发送按下
    ctypes.windll.user32.keybd_event(VK_HOME, sc, flag_down, 0)
    time.sleep(0.05)

    state = ctypes.windll.user32.GetAsyncKeyState(VK_HOME)
    is_down = bool(state & 0x8000)
    was_pressed = bool(state & 0x0001)

    # 发送抬起
    ctypes.windll.user32.keybd_event(VK_HOME, sc, flag_down | KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)

    print(f"[{label}]")
    print(f"  GetAsyncKeyState = 0x{state & 0xFFFF:04X}")
    print(f"  当前按下: {is_down}, 曾按下: {was_pressed}")
    print()

# 测试 2：对比一个已知正常的键（PageUp）
VK_PGUP = 0x21
print("--- 对比: PageUp (VK=0x21, SC=0x49, EXTENDEDKEY) ---")
ctypes.windll.user32.GetAsyncKeyState(VK_PGUP)
ctypes.windll.user32.keybd_event(VK_PGUP, 0x49, KEYEVENTF_EXTENDEDKEY, 0)
time.sleep(0.05)
state = ctypes.windll.user32.GetAsyncKeyState(VK_PGUP)
ctypes.windll.user32.keybd_event(VK_PGUP, 0x49, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
time.sleep(0.05)
print(f"  GetAsyncKeyState = 0x{state & 0xFFFF:04X}")
print(f"  当前按下: {bool(state & 0x8000)}, 曾按下: {bool(state & 0x0001)}")

# 测试 3：对比一个字母键
print()
print("--- 对比: 字母键 A (VK=0x41) ---")
ctypes.windll.user32.GetAsyncKeyState(0x41)
ctypes.windll.user32.keybd_event(0x41, 0x1E, 0, 0)
time.sleep(0.05)
state = ctypes.windll.user32.GetAsyncKeyState(0x41)
ctypes.windll.user32.keybd_event(0x41, 0x1E, KEYEVENTF_KEYUP, 0)
time.sleep(0.05)
print(f"  GetAsyncKeyState = 0x{state & 0xFFFF:04X}")
print(f"  当前按下: {bool(state & 0x8000)}, 曾按下: {bool(state & 0x0001)}")

print("\n=== 完成 ===")
input("按 Enter 退出...")
