"""清理标注 - 直接重命名目录"""
import shutil
from pathlib import Path

BASE = Path(r"F:\Program Files\WorkBuddy Works\python-test\脚本开发工具\YOLO自动标注工具")
labels_dir = BASE / "dataset" / "labels" / "train"
history_dir = BASE / "dataset" / "history"

# 重命名原目录为备份
backup_labels = labels_dir.parent / "train_bak"
backup_history = history_dir.parent / "history_bak"

if backup_labels.exists():
    shutil.rmtree(str(backup_labels))
labels_dir.rename(str(backup_labels))
labels_dir.mkdir(parents=True)

if history_dir.exists():
    if backup_history.exists():
        shutil.rmtree(str(backup_history))
    history_dir.rename(str(backup_history))

# 重新创建空目录
history_dir.mkdir(parents=True, exist_ok=True)

remaining = len(list(labels_dir.glob("*.txt")))
print(f"剩余标注: {remaining}")
print("完成! 旧数据备份到 train_bak/ 和 history_bak/")
