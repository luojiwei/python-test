"""
============================================================
YOLOv11 训练脚本
============================================================
用法：
    python scripts/train.py

或者指定参数：
    python scripts/train.py --model yolo11s.pt --epochs 100 --batch 16

设备选择：
    python scripts/train.py --device auto     # 自动检测 GPU（默认）
    python scripts/train.py --device cpu      # 强制 CPU
    python scripts/train.py --device 0        # 指定 GPU 编号（NVIDIA CUDA）

可用的预训练模型（从小到大）：
    yolo11n.pt  (nano,  最快, 精度最低)
    yolo11s.pt  (small, 推荐入门)
    yolo11m.pt  (medium)
    yolo11l.pt  (large)
    yolo11x.pt  (xlarge, 最慢, 精度最高)

训练完成后自动导出 ONNX 模型（用于 GPU 加速推理）。
============================================================
"""

import argparse
import shutil
import sys
from pathlib import Path

# GPU 检测模块（位于项目根目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gpu_utils
from gpu_utils import resolve_device
from config import MODELS_DIR as MODELS_ROOT

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
    parser.add_argument("--device", default="auto",
                        help="设备: auto (自动检测) / cpu / 0 (GPU编号) / cuda:0")
    parser.add_argument("--patience", type=int, default=20, help="早停 patience (默认: 20)")
    parser.add_argument("--resume", action="store_true", help="从中断处恢复训练")
    parser.add_argument("--workers", type=int, default=0, help="数据加载线程数 (Windows建议0)")
    parser.add_argument("--mosaic", type=float, default=1.0, help="Mosaic 增强比例 (0=关闭, 1=全开)")
    parser.add_argument("--hsv_h", type=float, default=0.015, help="HSV-Hue 增强")
    parser.add_argument("--hsv_s", type=float, default=0.7, help="HSV-Saturation 增强")
    parser.add_argument("--hsv_v", type=float, default=0.4, help="HSV-Value 增强")
    parser.add_argument("--scale", type=float, default=0.5, help="随机缩放幅度")

    args = parser.parse_args()

    # 解析设备（auto → 自动检测 GPU）
    resolved_device = resolve_device(args.device)
    gpu = gpu_utils.detect_gpu()

    dataset_yaml = Path(args.data) if args.data else DEFAULT_DATASET_YAML
    output_dir = Path(args.project) if args.project else DEFAULT_OUTPUT_DIR

    if not dataset_yaml.exists():
        print(f"[错误] 数据集配置文件不存在: {dataset_yaml}")
        print("请确保 dataset/data.yaml 存在并正确配置。")
        sys.exit(1)

    # ---- 加载模型 ----
    print(f"\n[1/4] 加载模型: {args.model}")
    model = YOLO(args.model)

    # ---- 开始训练 ----
    print(f"\n[2/4] 开始训练...")
    print(f"      数据集: {dataset_yaml}")
    print(f"      输出: {output_dir}")
    print(f"      轮数: {args.epochs}")
    print(f"      批次: {args.batch}")
    print(f"      设备: {resolved_device} (原始: {args.device})")
    if gpu["type"] != "none":
        print(f"      GPU: {gpu['name']} ({gpu['backend']})")
    else:
        print(f"      GPU: 未检测到，使用 CPU 训练")

    results = model.train(
        data=str(dataset_yaml),
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        lr0=args.lr0,
        device=resolved_device,
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

    best_pt = Path(results.save_dir) / "weights" / "best.pt"

    # ---- 导出 ONNX 模型（用于 GPU 加速推理） ----
    print(f"\n[3/4] 导出 ONNX 模型...")
    try:
        onnx_path = model.export(format="onnx", imgsz=args.imgsz, simplify=True)
        print(f"      ONNX 模型: {onnx_path}")

        # 验证 GPU 推理是否可用
        if gpu["type"] != "none":
            gpu_utils.patch_onnx_for_gpu()
            print(f"      GPU 推理: {gpu_utils.get_gpu_status_text()}")
        else:
            print(f"      GPU 推理: 未检测到 GPU，ONNX 将使用 CPU 推理")
    except Exception as e:
        print(f"      [警告] ONNX 导出失败: {e}")
        print(f"      PyTorch .pt 模型仍可正常使用")

    # ---- 输出结果 ----
    print(f"\n[4/4] 训练完成！")
    print(f"      最佳模型: {best_pt}")
    print(f"      最终模型: {Path(results.save_dir) / 'weights' / 'last.pt'}")
    print(f"      训练结果: {results.save_dir}/")
    onnx_pt = best_pt.with_suffix(".onnx")
    if onnx_pt.exists():
        print(f"      ONNX 模型: {onnx_pt}")

    # ---- 自动保存到 trained_models/ ----
    trained_dir = MODELS_ROOT
    trained_dir.mkdir(parents=True, exist_ok=True)
    existing_versions = sorted([int(f.stem[1:]) for f in trained_dir.glob("v*.pt") if f.stem[1:].isdigit()])
    next_ver = existing_versions[-1] + 1 if existing_versions else 1
    save_pt = trained_dir / f"v{next_ver}.pt"
    save_onnx = trained_dir / f"v{next_ver}.onnx"
    shutil.copy2(best_pt, save_pt)
    if onnx_pt.exists():
        shutil.copy2(onnx_pt, save_onnx)
    print(f"\n已保存: {save_pt}")
    if save_onnx.exists():
        print(f"已保存: {save_onnx}")
    print(f"下一步: python scripts/validate.py --weights {save_pt}")


if __name__ == "__main__":
    main()
