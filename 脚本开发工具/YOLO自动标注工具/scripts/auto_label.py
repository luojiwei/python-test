"""auto_label.py — YOLO 自举训练后的自动标注脚本

用训练好的模型（outputs/results/train/weights/best.pt）对新图片进行自动标注。
优先使用 ONNX + DirectML GPU 加速推理。

用法:
    python scripts/auto_label.py --weights outputs/results/train/weights/best.pt \
        --source screenshots/ --output dataset/labels/train \
        --conf 0.3 --img-width 1280 --img-height 720 --device auto
"""

import sys
import argparse
import time
from datetime import datetime
from pathlib import Path

# GPU 检测模块（位于项目根目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gpu_utils

from ultralytics import YOLO


def _log(msg: str) -> None:
    """带时间戳的日志输出。"""
    ts = datetime.now().strftime("[%H:%M:%S.%f]")[:-3]
    print(f"{ts} {msg}", flush=True)


def load_model(weights: Path, device: str = "auto") -> "YOLO":
    """加载模型，优先使用 ONNX + GPU 加速。"""
    gpu = gpu_utils.detect_gpu()
    use_onnx = gpu_utils.should_use_onnx(device) or gpu["type"] == "directml"

    _log(f"load_model: weights={weights}, device={device}, use_onnx={use_onnx}, gpu={gpu['type']}")

    # 启用 DirectML patch（对 ONNX 模型生效）
    if gpu_utils.patch_onnx_for_gpu():
        _log("DirectML GPU 推理 patch 已启用")

    if use_onnx:
        onnx_file = weights.with_suffix(".onnx")
        if not onnx_file.exists():
            # 自动导出 ONNX
            try:
                _log(f"ONNX 不存在，开始导出: {weights}")
                t0 = time.time()
                model_pt = YOLO(str(weights))
                onnx_path = model_pt.export(format="onnx", simplify=True)
                onnx_file = Path(onnx_path) if isinstance(onnx_path, str) else onnx_file
                _log(f"ONNX 导出完成 ({time.time() - t0:.1f}s): {onnx_file}")
            except Exception as e:
                _log(f"[警告] ONNX 导出失败，回退到 .pt: {e}")
                onnx_file = None

        if onnx_file and onnx_file.exists():
            _log(f"加载 ONNX 模型: {onnx_file}")
            t0 = time.time()
            model = YOLO(str(onnx_file))
            _log(f"ONNX 模型加载完成 ({time.time() - t0:.1f}s)")
            return model

    _log(f"加载 PyTorch 模型: {weights}")
    t0 = time.time()
    model = YOLO(str(weights))
    _log(f"PyTorch 模型加载完成 ({time.time() - t0:.1f}s)")
    return model


def main():
    parser = argparse.ArgumentParser(description="YOLO 自动标注（自举训练后）")
    parser.add_argument("--weights", required=True, help="模型权重路径 (best.pt 或 best.onnx)")
    parser.add_argument("--source", required=True, help="图片目录")
    parser.add_argument("--output", required=True, help="标注输出目录 (YOLO .txt)")
    parser.add_argument("--conf", type=float, default=0.3, help="置信度阈值")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU 阈值")
    parser.add_argument("--img-width", type=int, default=1280, help="图片宽度")
    parser.add_argument("--img-height", type=int, default=720, help="图片高度")
    parser.add_argument("--device", default="auto",
                         help="设备: auto / cpu / cuda:0 / dml:0")
    parser.add_argument("--skip-existing", action="store_true",
                         help="跳过已有标注的图片")
    args = parser.parse_args()

    t_start = time.time()

    weights_path = Path(args.weights)
    source_dir = Path(args.source)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 查找模型文件
    if not weights_path.exists():
        onnx_alt = weights_path.with_suffix(".onnx")
        if onnx_alt.exists():
            _log(f".pt 不存在，使用 .onnx: {onnx_alt}")
            weights_path = onnx_alt
        else:
            _log(f"[错误] 模型文件不存在: {weights_path}")
            sys.exit(1)

    # 收集图片
    images = sorted([f for f in source_dir.iterdir()
                     if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
    _log(f"图片目录: {source_dir}，共 {len(images)} 张")

    if not images:
        _log("图片池为空，退出")
        return

    # 跳过已标注
    if args.skip_existing:
        labeled_stems = {f.stem for f in output_dir.glob("*.txt")}
        skip_count = len(labeled_stems)
        images = [img for img in images if img.stem not in labeled_stems]
        _log(f"跳过已标注: {skip_count} 张，剩余: {len(images)} 张")

    if not images:
        _log("所有图片已标注，退出")
        return

    # GPU 信息
    gpu = gpu_utils.detect_gpu()
    _log(f"GPU: type={gpu['type']}, name={gpu['name']}, backend={gpu['backend']}")
    _log(f"设备: {args.device} → resolved={gpu_utils.resolve_device(args.device)}")

    # 加载模型
    _log(f"开始加载模型...")
    model = load_model(weights_path, device=args.device)
    _log(f"模型就绪，开始推理 {len(images)} 张图片")

    # 推理
    resolved_device = gpu_utils.resolve_device(args.device)

    total_boxes = 0
    labeled_count = 0
    empty_count = 0
    error_count = 0
    t_batch_start = time.time()

    for i, img_path in enumerate(images):
        try:
            t_img = time.time()
            results = model(str(img_path), conf=args.conf, iou=args.iou,
                           verbose=False, device=resolved_device)
            dt = time.time() - t_img

            boxes = []
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes:
                    cls_id = int(box.cls)
                    xyxy = box.xyxy.tolist()[0]
                    x1, y1, x2, y2 = xyxy
                    w_img, h_img = float(args.img_width), float(args.img_height)
                    cx = ((x1 + x2) / 2) / w_img
                    cy = ((y1 + y2) / 2) / h_img
                    bw = (x2 - x1) / w_img
                    bh = (y2 - y1) / h_img
                    cx = max(0.0, min(1.0, cx))
                    cy = max(0.0, min(1.0, cy))
                    bw = max(0.0001, min(1.0, bw))
                    bh = max(0.0001, min(1.0, bh))
                    boxes.append(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

            label_path = output_dir / f"{img_path.stem}.txt"
            label_path.write_text("\n".join(boxes) + "\n" if boxes else "",
                                  encoding="utf-8")

            total_boxes += len(boxes)
            if boxes:
                labeled_count += 1
            else:
                empty_count += 1

            if (i + 1) % 20 == 0 or i == len(images) - 1:
                elapsed = time.time() - t_batch_start
                fps = (i + 1) / elapsed if elapsed > 0 else 0
                _log(f"进度: {i+1}/{len(images)} "
                     f"(框={total_boxes}, 有={labeled_count}, 空={empty_count}, "
                     f"错={error_count}, {fps:.1f} 张/秒)")

        except Exception as e:
            _log(f"[错误] {img_path.name}: {e}")
            (output_dir / f"{img_path.stem}.txt").write_text("", encoding="utf-8")
            error_count += 1

    total_time = time.time() - t_start
    _log(f"========== 标注完成 ==========")
    _log(f"总耗时: {total_time:.1f}s ({total_time/60:.1f}min)")
    _log(f"图片: {len(images)} 张")
    _log(f"有检出: {labeled_count} 张")
    _log(f"无检出: {empty_count} 张")
    _log(f"错误: {error_count} 张")
    _log(f"总框数: {total_boxes} 个")
    _log(f"速度: {len(images)/total_time:.1f} 张/秒")
    _log(f"标注文件: {output_dir}/")


if __name__ == "__main__":
    main()
