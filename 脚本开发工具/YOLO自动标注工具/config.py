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

# 审查缓存文件：记录已审查（待训练）图片的 stem
REVIEWED_CACHE_FILE = LABELS_TRAIN_DIR / ".reviewed"

# 已训练缓存文件：记录已训练完成（历史审核）图片的 stem
TRAINED_CACHE_FILE = LABELS_TRAIN_DIR / ".trained"


def load_reviewed_stems() -> set[str]:
    """加载已审查（待训练）的图片 stem 集合。"""
    if REVIEWED_CACHE_FILE.exists():
        return set(REVIEWED_CACHE_FILE.read_text(encoding="utf-8").strip().splitlines())
    return set()


def save_reviewed_stems(stems: set[str]) -> None:
    """保存审查缓存（已审核，待训练）。"""
    REVIEWED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_reviewed_stems()
    existing.update(stems)
    REVIEWED_CACHE_FILE.write_text("\n".join(sorted(existing)), encoding="utf-8")


def load_trained_stems() -> set[str]:
    """加载已训练完成（历史审核）的图片 stem 集合。"""
    if TRAINED_CACHE_FILE.exists():
        return set(TRAINED_CACHE_FILE.read_text(encoding="utf-8").strip().splitlines())
    return set()


def save_trained_stems(stems: set[str]) -> None:
    """保存已训练缓存（历史审核，累积）。"""
    TRAINED_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_trained_stems()
    existing.update(stems)
    TRAINED_CACHE_FILE.write_text("\n".join(sorted(existing)), encoding="utf-8")


def mark_reviewed_as_trained() -> int:
    """训练完成后：把当前已审核（待训练）图片归档为已训练（历史审核），并清空已审核缓存。

    返回本次归档的图片数量。
    """
    reviewed = load_reviewed_stems()
    if reviewed:
        save_trained_stems(reviewed)
    REVIEWED_CACHE_FILE.write_text("", encoding="utf-8")
    return len(reviewed)

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
WINDOW_TITLE: str = "冒险岛怀旧服"
# 截图统一为游戏窗口客户区原始尺寸（与自动脚本/地图标记工具一致）
TARGET_W: int = 1366
TARGET_H: int = 768
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
Image = None

def ensure_screenshot_libs() -> bool:
    global Image
    if Image is None:
        try:
            from PIL import Image as _Image
            Image = _Image
        except ImportError:
            from tkinter import messagebox
            messagebox.showerror("缺少依赖", "请先安装 Pillow:\npip install Pillow")
            return False
    return True
