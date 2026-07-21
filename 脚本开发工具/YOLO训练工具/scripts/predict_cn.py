"""
YOLOv11 推理（中文字体版）
用法: python scripts/predict_cn.py --weights ... --source ...
"""
import matplotlib
import matplotlib.font_manager as fm

# 删旧缓存，强制重建字体列表
fm._load_fontmanager(try_read_cache=False)

# 如果 SimHei 不可用，尝试微软雅黑
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'WenQuanYi Micro Hei']
plt.rcParams['axes.unicode_minus'] = False

# 确认加载成功
found = [f.name for f in fm.fontManager.ttflist if 'Hei' in f.name or 'YaHei' in f.name]
print(f"[Font] Available CJK fonts: {set(found)}")
print(f"[Font] Matplotlib default: {plt.rcParams['font.sans-serif']}")

import argparse
from pathlib import Path
from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def main():
    parser = argparse.ArgumentParser(description="YOLOv11 推理预测(中文)")
    parser.add_argument("--weights", required=True, help="模型权重路径 (best.pt)")
    parser.add_argument("--source", required=True, help="图片路径或文件夹")
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值 (默认: 0.25)")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU 阈值 (默认: 0.45)")
    parser.add_argument("--device", default="cpu", help="设备")
    args = parser.parse_args()

    weights_path = Path(args.weights)
    source_path = Path(args.source)
    if not weights_path.exists() or not source_path.exists():
        print(f"[ERROR] 文件不存在")
        return

    model = YOLO(str(weights_path))
    results = model.predict(
        source=str(source_path),
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        save=True,
        project=str(OUTPUT_DIR / "results"),
        name="predict_cn",
        exist_ok=True,
    )

    total = sum(len(r.boxes) if r.boxes is not None else 0 for r in results)
    print(f"\nDone: {total} objects detected")
    print(f"Saved: {OUTPUT_DIR / 'results' / 'predict_cn'}/")


if __name__ == "__main__":
    main()
