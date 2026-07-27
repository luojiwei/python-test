"""config.py — 全局配置、按键映射、地图发现"""

from pathlib import Path

import numpy as np

# ============================================================
# 路径 & 窗口
# ============================================================

PROJECT_DIR: Path = Path(__file__).resolve().parent
OUTPUT_DIR: Path = PROJECT_DIR / "track_output"
WINDOW_TITLE: str = "WingsMs"

# ============================================================
# 频率 & 时间
# ============================================================

YOLO_CONF: float = 0.60
YOLO_IOU: float = 0.45
YOLO_INTERVAL: float = 0.5
PERCEPTION_INTERVAL: float = 0.1
TICK_INTERVAL: float = 0.03
LOGIC_INTERVAL: float = 1.0

# ============================================================
# 战斗
# ============================================================

ATTACK_DISTANCE: int = 200
ATTACK_VERTICAL: int = 80
PLATFORM_TOLERANCE: int = 75
JUMP_THRESHOLD: int = -30
ATTACK_PULSE: float = 0.30

# ============================================================
# 模板匹配
# ============================================================

MATCH_THRESHOLD: float = 0.50
SEARCH_BOTTOM_SKIP_PCT: float = 0.10

# ============================================================
# 爬梯 & 移动
# ============================================================

CLIMB_TIMEOUT: float = 6.0
CLIMB_NO_MONSTER_WAIT: float = 3.0
CLIMB_OVERSHOOT: float = 0.5
POSITION_THRESHOLD: int = 2
MOUNT_DURATION: float = 0.8
JUMP_TIMEOUT: float = 3.0
FLASH_TIMEOUT: float = 2.0
MOVE_TIMEOUT: float = 5.0

# ============================================================
# 小地图黄点
# ============================================================

DOT_HSV_LOWER: np.ndarray = np.array([25, 100, 180])
DOT_HSV_UPPER: np.ndarray = np.array([35, 255, 255])

# ============================================================
# 虚拟键码
# ============================================================

VK_LEFT, VK_UP, VK_RIGHT = 0x25, 0x26, 0x27
VK_ALT, VK_CTRL, VK_DOWN = 0x12, 0x11, 0x28
SCAN_LEFT, SCAN_UP, SCAN_RIGHT = 0x4B, 0x48, 0x4D
SCAN_ALT, SCAN_CTRL, SCAN_DOWN = 0x38, 0x1D, 0x50

VK_SHIFT = 0xA0
SCAN_SHIFT = 0x2A

KEY_MAP: dict[str, tuple[int, int]] = {
    # 移动 / 攻击
    'l': (VK_LEFT, SCAN_LEFT), 'r': (VK_RIGHT, SCAN_RIGHT),
    'u': (VK_UP, SCAN_UP), 'd': (VK_DOWN, SCAN_DOWN),
    'j': (VK_ALT, SCAN_ALT), 'a': (VK_CTRL, SCAN_CTRL),
    # 修饰键
    'ctrl': (VK_CTRL, SCAN_CTRL), 'shift': (VK_SHIFT, SCAN_SHIFT),
    # Buff 键位
    'pgup': (0x21, 0x49), 'pgdn': (0x22, 0x51),
    'home': (0x24, 0x00), 'end':   (0x23, 0x4F),
    'ins':  (0x2D, 0x52), 'del':   (0x2E, 0x53),
    'f1':  (0x70, 0x3B), 'f2':  (0x71, 0x3C), 'f3':  (0x72, 0x3D), 'f4':  (0x73, 0x3E),
    'f5':  (0x74, 0x3F), 'f6':  (0x75, 0x40), 'f7':  (0x76, 0x41), 'f8':  (0x77, 0x42),
    'f9':  (0x78, 0x43), 'f10': (0x79, 0x44), 'f11': (0x7A, 0x57), 'f12': (0x7B, 0x58),
    'num0': (0x30, 0x0B), 'num1': (0x31, 0x02), 'num2': (0x32, 0x03), 'num3': (0x33, 0x04),
    'num4': (0x34, 0x05), 'num5': (0x35, 0x06), 'num6': (0x36, 0x07), 'num7': (0x37, 0x08),
    'num8': (0x38, 0x09), 'num9': (0x39, 0x0A),
    # 字母 A-Z（大写区分于移动键）
    'A': (0x41, 0x1E), 'B': (0x42, 0x30), 'C': (0x43, 0x2E),
    'D': (0x44, 0x20), 'E': (0x45, 0x12), 'F': (0x46, 0x21),
    'G': (0x47, 0x22), 'H': (0x48, 0x23), 'I': (0x49, 0x17),
    'J': (0x4A, 0x24), 'K': (0x4B, 0x25), 'L': (0x4C, 0x26),
    'M': (0x4D, 0x32), 'N': (0x4E, 0x31), 'O': (0x4F, 0x18),
    'P': (0x50, 0x19), 'Q': (0x51, 0x10), 'R': (0x52, 0x13),
    'S': (0x53, 0x1F), 'T': (0x54, 0x14), 'U': (0x55, 0x16),
    'V': (0x56, 0x2F), 'W': (0x57, 0x11), 'X': (0x58, 0x2D),
    'Y': (0x59, 0x15), 'Z': (0x5A, 0x2C),
}

# Buff 键位下拉选项 (显示名, 内部键名)
SKILL_KEY_CHOICES: list[tuple[str, str]] = [
    ("无", ""),
    ("Ctrl", "ctrl"), ("Shift", "shift"),
    ("PageUp", "pgup"), ("PageDown", "pgdn"),
    ("Home", "home"), ("End", "end"),
    ("Insert", "ins"), ("Delete", "del"),
    ("F1", "f1"), ("F2", "f2"), ("F3", "f3"), ("F4", "f4"),
    ("F5", "f5"), ("F6", "f6"), ("F7", "f7"), ("F8", "f8"),
    ("F9", "f9"), ("F10", "f10"), ("F11", "f11"), ("F12", "f12"),
    ("1", "num1"), ("2", "num2"), ("3", "num3"), ("4", "num4"),
    ("5", "num5"), ("6", "num6"), ("7", "num7"), ("8", "num8"),
    ("9", "num9"), ("0", "num0"),
    # 字母 A-Z
    ("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("E", "E"),
    ("F", "F"), ("G", "G"), ("H", "H"), ("I", "I"), ("J", "J"),
    ("K", "K"), ("L", "L"), ("M", "M"), ("N", "N"), ("O", "O"),
    ("P", "P"), ("Q", "Q"), ("R", "R"), ("S", "S"), ("T", "T"),
    ("U", "U"), ("V", "V"), ("W", "W"), ("X", "X"), ("Y", "Y"),
    ("Z", "Z"),
]

SKILL_KEY_LOOKUP: dict[str, str] = {d: i for d, i in SKILL_KEY_CHOICES}

# 技能安全余量：在技能到期前 N 秒提前刷新
SKILL_SAFETY_MARGIN: float = 5.0

# ============================================================
# 职业配置
# ============================================================

SKILL_RULE_CHOICES: list[tuple[str, str]] = [
    ("混合使用", "mixed"),
    ("只用单体", "single"),
    ("只用群体", "aoe"),
]
SKILL_RULE_DISPLAY_TO_CODE: dict[str, str] = {d: c for d, c in SKILL_RULE_CHOICES}
SKILL_RULE_CODE_TO_DISPLAY: dict[str, str] = {c: d for d, c in SKILL_RULE_CHOICES}


def _load_occupation_data() -> dict[str, dict]:
    """从 occupation_skills.json 加载职业技能模板。"""
    import json
    json_path = Path(__file__).parent / "occupation_skills.json"
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


OCCUPATION_DATA: dict[str, dict] = _load_occupation_data()

# ============================================================
# YOLO 类别（运行时从模型读取）
# ============================================================

CLASS_NAMES: dict[int, str] = {}
# 黑名单：这些名称的类别不会被当作怪物
NON_MONSTER_NAMES: set[str] = {"绳子上", "绳子下", "梯子上", "梯子下"}

# ============================================================
# 地图发现
# ============================================================

def discover_maps() -> list[str]:
    """扫描 maps/ 目录，返回所有有 config.json 的地图名"""
    maps_dir = PROJECT_DIR / "maps"
    if not maps_dir.is_dir():
        return []
    result = []
    for d in sorted(maps_dir.iterdir()):
        if d.is_dir() and (d / "config.json").exists():
            result.append(d.name)
    return result


def validate_map_resources(map_name: str) -> list[str]:
    """验证地图资源完整性，返回缺失文件列表"""
    map_dir = PROJECT_DIR / "maps" / map_name
    required = ["config.json", "world_model.json", "best.pt"]
    missing = [f for f in required if not (map_dir / f).exists()]
    return missing
