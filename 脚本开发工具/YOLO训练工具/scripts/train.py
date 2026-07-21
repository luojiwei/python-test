"""
============================================================
YOLOv11 训练脚本
============================================================
用法：
    python scripts/train.py

或者指定参数：
    python scripts/train.py --model yolo11s.pt --epochs 100 --batch 16

可用的预训练模型（从小到大）：
    yolo11n.pt  (nano,  最快, 精度最低)
    yolo11s.pt  (small, 推荐入门)
    yolo11m.pt  (medium)
    yolo11l.pt  (large)
    yolo11x.pt  (xlarge, 最慢, 精度最高)
============================================================
"""

import argparse
import sys
from pathlib import Path

from ultralytics import YOLO

# 项目根目录（scripts 的上一级）— 默认值，可由 --data/--project 覆盖
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_YAML = PROJECT_ROOT / "dataset" / "data.yaml"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


def main():
    parser = argparse.ArgumentParser(description="YOLOv11 训练")
    parser.add_argument("--model", default="yolo11n.pt", help="预训练模型 (默认: yolo11n.pt)")
    parser.add_argument("--data", default=None, help="数据集 yaml 路径 (默认: dataset/data.yaml)")
    parser.add_argument("--project", default=None, help="输出目录 (默认: outputs/)")
    parser.add_argument("--epochs", type=int, default=100, help="训练轮数 (默认: 100)")
    parser.add_argument("--batch", type=int, default=16, help="批次大小 (默认: 16, CPU建议4-8)")
    parser.add_argument("--imgsz", type=int, default=640, help="输入图片尺寸 (默认: 640)")
    parser.add_argument("--lr0", type=float, default=0.01, help="初始学习率 (默认: 0.01)")
    parser.add_argument("--device", default="cpu", help="设备: cpu / 0 (GPU编号) / mps")
    parser.add_argument("--patience", type=int, default=20, help="早停 patience (默认: 20)")
    parser.add_argument("--resume", action="store_true", help="从中断处恢复训练")
    parser.add_argument("--workers", type=int, default=0, help="数据加载线程数 (Windows建议0)")
    parser.add_argument("--mosaic", type=float, default=1.0, help="Mosaic 增强比例 (0=关闭, 1=全开)")
    parser.add_argument("--hsv_h", type=float, default=0.015, help="HSV-Hue 增强")
    parser.add_argument("--hsv_s", type=float, default=0.7, help="HSV-Saturation 增强")
    parser.add_argument("--hsv_v", type=float, default=0.4, help="HSV-Value 增强")
    parser.add_argument("--scale", type=float, default=0.5, help="随机缩放幅度")

    args = parser.parse_args()

    dataset_yaml = Path(args.data) if args.data else DEFAULT_DATASET_YAML
    output_dir = Path(args.project) if args.project else DEFAULT_OUTPUT_DIR

    if not dataset_yaml.exists():
        print(f"[错误] 数据集配置文件不存在: {dataset_yaml}")
        print("请确保 dataset/data.yaml 存在并正确配置。")
        sys.exit(1)

    # ---- 加载模型 ----
    print(f"\n[1/3] 加载模型: {args.model}")
    model = YOLO(args.model)

    # ---- 开始训练 ----
    print(f"\n[2/3] 开始训练...")
    print(f"      数据集: {dataset_yaml}")
    print(f"      输出: {output_dir}")
    print(f"      轮数: {args.epochs}")
    print(f"      批次: {args.batch}")
    print(f"      设备: {args.device}")

    results = model.train(
        data=str(dataset_yaml),
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        lr0=args.lr0,
        device=args.device,
        patience=args.patience,
        resume=args.resume,
        workers=args.workers,
        project=str(output_dir / "results"),
        name="train",
        exist_ok=True,
        # 数据增强
        hsv_h=args.hsv_h,
        hsv_s=args.hsv_s,
        hsv_v=args.hsv_v,
        degrees=10.0,    # 随机旋转
        translate=0.1,   # 随机平移
        scale=args.scale,
        fliplr=0.5,      # 水平翻转
        mosaic=args.mosaic,
        # 保存
        save=True,
        save_period=10,  # 每10轮保存一次
    )

    # ---- 输出结果 ----
    print(f"\n[3/3] 训练完成！")
    print(f"      最佳模型: {results.save_dir}/weights/best.pt")
    print(f"      最终模型: {results.save_dir}/weights/last.pt")
    print(f"      训练结果: {results.save_dir}/")
    print(f"\n下一步: python scripts/validate.py --weights {results.save_dir}/weights/best.pt")


if __name__ == "__main__":
    main()
