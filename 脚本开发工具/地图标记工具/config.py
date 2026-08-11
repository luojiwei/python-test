"""地图标记工具 — 全局配置常量。"""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "marker_output"
SYSTEM_SETTINGS_FILE = OUTPUT_DIR / "system_setting.json"
WINDOW_TITLE = "WingsMs"
CAPTURE_FPS = 20


def _safe_filename(name: str) -> str:
    """将字符串中的 Windows 非法字符替换为下划线。"""
    bad = '<>:"/\\|?*'
    out = name
    for ch in bad:
        out = out.replace(ch, '_')
    return out


def get_map_path(window_name: str, map_name: str) -> Path:
    """获取地图数据文件路径: marker_output/{窗口名}/{地图名}_maps.json"""
    return OUTPUT_DIR / _safe_filename(window_name) / f"{_safe_filename(map_name)}_maps.json"


def get_model_path(window_name: str, map_name: str) -> Path:
    """获取世界模型文件路径: marker_output/{窗口名}/{地图名}_model.json"""
    return OUTPUT_DIR / _safe_filename(window_name) / f"{_safe_filename(map_name)}_model.json"
