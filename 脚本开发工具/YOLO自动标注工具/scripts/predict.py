"""
============================================================
YOLOv11 推理/预测脚本
用训练好的模型对新图片进行检测，直观看到效果
============================================================
用法：
    # 单张图片
    python scripts/predict.py --weights outputs/results/train/weights/best.pt --source screenshots/test.png

    # 整个文件夹
    python scripts/predict.py --weights outputs/results/train/weights/best.pt --source screenshots/

    # 置信度阈值设为0.5（只显示高置信度检测结果）
    python scripts/predict.py --weights outputs/results/train/weights/best.pt --source screenshots/ --conf 0.5

    # 保存结果（不显示窗口）
    python scripts/predict.py --weights outputs/results/train/weights/best.pt --source screenshots/ --noshow
============================================================
"""

import argparse
from pathlib import Path

from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


def main():
    parser = argparse.ArgumentParser(description="YOLOv11 推理预测")
    parser.add_argument("--weights", required=True, help="模型权重路径 (best.pt)")
    parser.add_argument("--source", required=True, help="图片路径或文件夹")
    parser.add_argument("--project", default=None, help="输出目录")
    parser.add_argument("--conf", type=float, default=0.25, help="置信度阈值 (默认: 0.25)")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU 阈值 (默认: 0.45)")
    parser.add_argument("--device", default="auto",
                        help="设备: auto (自动检测) / cpu / 0 (GPU编号)")
    parser.add_argument("--noshow", action="store_true", help="不弹窗显示，只保存文件")
    parser.add_argument("--save-txt", action="store_true", help="保存检测结果为 txt 文件")
    args = parser.parse_args()

    weights_path = Path(args.weights)
    source_path = Path(args.source)
    output_dir = Path(args.project) if args.project else DEFAULT_OUTPUT_DIR

    if not weights_path.exists():
        print(f"[错误] 模型文件不存在: {weights_path}")
        return
    if not source_path.exists():
        print(f"[错误] 图片路径不存在: {source_path}")
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

    print(f"\n推理中...")
    print(f"  来源: {source_path}")
    print(f"  置信度阈值: {args.conf}")
    print(f"  设备: {resolved_device} (原始: {args.device})")

    results = model.predict(
        source=str(source_path),
        conf=args.conf,
        iou=args.iou,
        device=resolved_device,
        save=True,
        save_txt=args.save_txt,
        project=str(output_dir / "results"),
        name="predict",
        exist_ok=True,
    )

    # 汇总结果
    total_objects = 0
    for r in results:
        total_objects += len(r.boxes) if r.boxes is not None else 0

    save_dir = output_dir / "results" / "predict"
    print(f"\n完成！检测到 {total_objects} 个目标")
    print(f"结果保存在: {save_dir}/")


if __name__ == "__main__":
    main()
