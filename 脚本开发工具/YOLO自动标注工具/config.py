"""config.py — 全局路径与常量"""

from pathlib import Path

# ============================================================
# 路径
# ============================================================
PROJECT_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = PROJECT_DIR / "screenshots"
DATASET_DIR = PROJECT_DIR / "dataset"
IMAGES_TRAIN_DIR = DATASET_DIR / "images" / "train"
IMAGES_VAL_DIR = DATASET_DIR / "images" / "val"
LABELS_TRAIN_DIR = DATASET_DIR / "labels" / "train"
LABELS_VAL_DIR = DATASET_DIR / "labels" / "val"
DATA_YAML = DATASET_DIR / "data.yaml"
OUTPUTS_DIR = PROJECT_DIR / "outputs"
MODELS_DIR = PROJECT_DIR / "trained_models"
SCRIPTS_DIR = PROJECT_DIR / "scripts"
CONFIG_FILE = PROJECT_DIR / "config_cache.json"

# ============================================================
# 截图参数
# ============================================================
WINDOW_TITLE: str = "WingsMs"
TARGET_W: int = 1280
TARGET_H: int = 720
IMAGE_FORMAT: str = "PNG"
INTERVAL: float = 1.0

# ============================================================
# Python 解释器路径
# ============================================================
YOLO_PYTHON = Path(
    "C:/Users/Administrator/.workbuddy/binaries/python/envs/yolo/Scripts/python.exe"
)
GDINO_PYTHON = Path(
    "C:/Users/Administrator/.workbuddy/binaries/python/envs/gdino/Scripts/python.exe"
)
PYTHON_BIN = Path(
    "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
)

# ============================================================
# 外部依赖 — 延迟导入
# ============================================================
mss = None
Image = None

def ensure_screenshot_libs() -> bool:
    global mss, Image
    if mss is None:
        try:
            import mss as _mss
            mss = _mss
        except ImportError:
            from tkinter import messagebox
            messagebox.showerror("缺少依赖", "请先安装 mss:\npip install mss")
            return False
    if Image is None:
        try:
            from PIL import Image as _Image
            Image = _Image
        except ImportError:
            from tkinter import messagebox
            messagebox.showerror("缺少依赖", "请先安装 Pillow:\npip install Pillow")
            return False
    return True
