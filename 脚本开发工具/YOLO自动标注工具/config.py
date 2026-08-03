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

# 审查缓存文件：记录已审查图片的 stem
REVIEWED_CACHE_FILE = LABELS_TRAIN_DIR / ".reviewed"


def load_reviewed_stems() -> set[str]:
    """加载已审查的图片 stem 集合。"""
    if REVIEWED_CACHE_FILE.exists():
        return set(REVIEWED_CACHE_FILE.read_text(encoding="utf-8").strip().splitlines())
    return set()


def save_reviewed_stems(stems: set[str]) -> None:
    """保存审查缓存。"""
    REVIEWED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_reviewed_stems()
    existing.update(stems)
    REVIEWED_CACHE_FILE.write_text("\n".join(sorted(existing)), encoding="utf-8")

# 历史审查轮次
HISTORY_DIR = DATASET_DIR / "history"

def save_review_round(stems: set[str]) -> int:
    """保存一轮审查记录，返回轮次号。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    existing_rounds = sorted([int(f.stem.split("_")[1]) for f in HISTORY_DIR.glob("round_*.txt")])
    next_round = existing_rounds[-1] + 1 if existing_rounds else 1
    round_file = HISTORY_DIR / f"round_{next_round:03d}.txt"
    round_file.write_text("\n".join(sorted(stems)), encoding="utf-8")
    return next_round

def list_review_rounds() -> list[int]:
    """列出所有审查轮次。"""
    if not HISTORY_DIR.exists():
        return []
    return sorted([int(f.stem.split("_")[1]) for f in HISTORY_DIR.glob("round_*.txt")])

def load_review_round(round_num: int) -> set[str]:
    """加载指定轮次的 stems。"""
    round_file = HISTORY_DIR / f"round_{round_num:03d}.txt"
    if round_file.exists():
        return set(round_file.read_text(encoding="utf-8").strip().splitlines())
    return set()

OUTPUTS_DIR = PROJECT_DIR / "outputs"
MODELS_DIR = PROJECT_DIR / "trained_models"

def get_available_models() -> list[Path]:
    """列出 trained_models/ 下所有 .pt 文件（仅当前目录，不含子目录）。"""
    if not MODELS_DIR.exists():
        return []
    return sorted([f for f in MODELS_DIR.iterdir() if f.suffix == ".pt"],
                  key=lambda f: f.stat().st_mtime, reverse=True)
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
