"""
============================================================
YOLOv11 验证/评估脚本
训练完成后，用验证集评估模型性能
============================================================
用法：
    python scripts/validate.py --weights outputs/results/train/weights/best.pt
============================================================
"""

import argparse
from pathlib import Path

from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_YAML = PROJECT_ROOT / "dataset" / "data.yaml"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


def main():
    parser = argparse.ArgumentParser(description="YOLOv11 模型验证")
    parser.add_argument("--weights", required=True, help="模型权重路径 (best.pt)")
    parser.add_argument("--data", default=None, help="数据集 yaml 路径")
    parser.add_argument("--project", default=None, help="输出目录")
    parser.add_argument("--device", default="auto",
                        help="设备: auto (自动检测) / cpu / 0 (GPU编号)")
    parser.add_argument("--imgsz", type=int, default=640, help="图片尺寸")
    parser.add_argument("--batch", type=int, default=16, help="批次大小")
    parser.add_argument("--conf", type=float, default=0.001, help="置信度阈值")
    parser.add_argument("--iou", type=float, default=0.6, help="IoU 阈值")
    parser.add_argument("--save-json", action="store_true", help="保存结果为 JSON")
    args = parser.parse_args()

    weights_path = Path(args.weights)
    dataset_yaml = Path(args.data) if args.data else DEFAULT_DATASET_YAML
    output_dir = Path(args.project) if args.project else DEFAULT_OUTPUT_DIR

    if not weights_path.exists():
        print(f"[错误] 模型文件不存在: {weights_path}")
        return

    print(f"\n加载模型: {weights_path}")
    model = YOLO(str(weights_path))

    # 解析设备
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    import gpu_utils
    resolved_device = gpu_utils.resolve_device(args.device)
    gpu_utils.patch_onnx_for_gpu()

    print(f"\n在验证集上评估...")
    print(f"  设备: {resolved_device} (原始: {args.device})")
    metrics = model.val(
        data=str(dataset_yaml),
        imgsz=args.imgsz,
        batch=args.batch,
        conf=args.conf,
        iou=args.iou,
        device=resolved_device,
        project=str(output_dir / "results"),
        name="val",
        exist_ok=True,
        save_json=args.save_json,
    )

    # ---- 解读关键指标 ----
    print("\n" + "=" * 60)
    print("验证结果解读")
    print("=" * 60)

    # mAP@50
    map50 = float(metrics.box.map50) if hasattr(metrics.box, 'map50') else 0
    print(f"  mAP@50  : {map50:.4f}  <- 最重要指标, 越高越好 (max=1.0)")
    if map50 >= 0.9:
        print("             >>> 非常好！")
    elif map50 >= 0.7:
        print("             >>> 不错，可用")
    elif map50 >= 0.5:
        print("             >>> 一般，建议继续优化")
    else:
        print("             >>> 偏低，检查数据和标注")

    # mAP@50-95
    map75 = float(metrics.box.map) if hasattr(metrics.box, 'map') else 0
    print(f"  mAP@50-95: {map75:.4f}  <- 更严格, 多个IoU阈值平均")

    # Precision & Recall
    p = float(metrics.box.mp) if hasattr(metrics.box, 'mp') else 0
    r = float(metrics.box.mr) if hasattr(metrics.box, 'mr') else 0
    print(f"  Precision: {p:.4f}  <- 预测为真的样本中, 有多少是真的")
    print(f"  Recall   : {r:.4f}  <- 所有真实目标中, 有多少被检测到")

    if p > 0.9 and r < 0.5:
        print("             >>> 检测偏保守 (漏检多)，可降低置信度阈值")
    elif r > 0.9 and p < 0.5:
        print("             >>> 检测偏激进 (误检多)，可提高置信度阈值")
    print("=" * 60)

    print(f"\n结果文件保存在: {metrics.save_dir}/")
    print(f"\n下一步: python scripts/predict.py --weights {args.weights} --source screenshots/ --conf 0.25")


if __name__ == "__main__":
    main()
