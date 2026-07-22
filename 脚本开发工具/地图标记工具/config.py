"""地图标记工具 — 全局配置常量。"""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "marker_output"
MAPS_FILE = OUTPUT_DIR / "maps.json"
WINDOW_TITLE = "WingsMs"
CAPTURE_FPS = 20
