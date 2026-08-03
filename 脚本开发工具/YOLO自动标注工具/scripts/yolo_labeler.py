"""yolo_labeler.py — 用已有 YOLO 模型批量自动标注

读取 trained_models/ 下的所有 .pt 模型，逐个推理，合并结果，输出 YOLO 标注。
所有怪物类统一映射为 class_id=0。

GPU 支持：
  - NVIDIA CUDA: 直接使用 PyTorch CUDA 推理
  - AMD/Intel (DirectML): 自动导出 ONNX 并使用 DirectML 加速推理
  - CPU: 使用 PyTorch CPU 推理

用法:
    python yolo_labeler.py <图片目录> <输出目录> [--device auto]
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from PIL import Image

from ultralytics import YOLO

# GPU 检测模块（位于项目根目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gpu_utils


def _log(msg: str) -> None:
    """带时间戳的日志输出。"""
    ts = datetime.now().strftime("[%H:%M:%S.%f]")[:-3]
    print(f"{ts} {msg}", flush=True)


MODELS_ROOT = Path(__file__).resolve().parent.parent / "trained_models"
DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "screenshots"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "dataset" / "labels" / "train"

TARGET_W, TARGET_H = 1280, 720
CONF = 0.3
IOU = 0.5


def load_models(models_root: Path, device: str = "auto",
                model_file: str = "") -> list[tuple[str, "YOLO", dict[int, str]]]:
    """加载训练模型。返回 [(名称, YOLO, 类别名), ...]

    如果指定 model_file 则只加载该文件，否则加载最新 .pt。
    优先加载 .onnx 模型（GPU DirectML 加速），如果没有则加载 .pt 并按需导出 ONNX。
    """
    models = []
    if not models_root.exists():
        return models

    # 确定要加载的 pt 文件
    if model_file:
        pt_files = [Path(model_file)]
        if not pt_files[0].exists():
            _log(f"指定模型不存在: {model_file}")
            return models
        _log(f"指定模型: {pt_files[0].name}")
    else:
        all_pt_files = sorted(models_root.rglob("*.pt"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not all_pt_files:
            return models
        pt_files = all_pt_files[:1]  # 只取最新一个
        _log(f"使用最新模型: {pt_files[0].name}")

    # 检测 GPU 类型
    gpu = gpu_utils.detect_gpu()
    use_onnx = gpu_utils.should_use_onnx(device) or gpu["type"] == "directml"
    resolved_device = gpu_utils.resolve_device(device)
    _log(f"load_models: device={device}, use_onnx={use_onnx}, gpu={gpu['type']}")

    # 启用 DirectML patch（对 ONNX 模型生效）
    if gpu_utils.patch_onnx_for_gpu():
        _log("DirectML GPU 推理 patch 已启用")

    for pt_file in pt_files:
        model_name = pt_file.parent.name if pt_file.parent != models_root else pt_file.stem

        # 先从 .pt 文件获取类别名（所有模型都需要）
        class_names: dict[int, str] = {}
        try:
            pt_yolo = YOLO(str(pt_file))
            if hasattr(pt_yolo.model, 'names') and isinstance(pt_yolo.model.names, dict):
                class_names = pt_yolo.model.names
            elif hasattr(pt_yolo, 'names'):
                class_names = pt_yolo.names
            _log(f"  {model_name}: 类别名 = {class_names}")
        except Exception as e:
            _log(f"  {model_name}: 无法读取类别名: {e}")

        if use_onnx:
            onnx_file = pt_file.with_suffix(".onnx")
            if not onnx_file.exists():
                try:
                    t0 = time.time()
                    _log(f"导出 ONNX: {model_name} ({pt_file.name})")
                    onnx_path = pt_yolo.export(format="onnx", simplify=True)  # 重用已加载的模型
                    onnx_file = Path(onnx_path) if isinstance(onnx_path, str) else onnx_file
                    _log(f"  ONNX 导出完成 ({time.time() - t0:.1f}s)")
                except Exception as e:
                    _log(f"[警告] ONNX 导出失败，回退到 .pt: {e}")
                    onnx_file = None

            if onnx_file and onnx_file.exists():
                try:
                    t0 = time.time()
                    yolo = YOLO(str(onnx_file))
                    models.append((model_name, yolo, class_names))
                    _log(f"  加载(ONNX): {model_name} ({onnx_file.name}) [GPU, {time.time()-t0:.1f}s]")
                    continue
                except Exception as e:
                    _log(f"[警告] ONNX 加载失败，回退到 .pt: {e}")

        # 回退到 PyTorch .pt (重用上面已加载的 pt_yolo)
        models.append((model_name, pt_yolo, class_names))
        backend = gpu["backend"] if gpu["type"] != "none" else "CPU"
        _log(f"  加载(PT): {model_name} ({pt_file.name}) [{backend}]")
    _log(f"load_models: 成功加载 {len(models)} 个模型（仅最新）")
    return models


def map_class(cls_name: str) -> int | None:
    """将各模型的类别名映射到统一 class_id。返回 None 表示跳过。
    统一映射:
        0 = 怪物
        1 = 绳子上
        2 = 绳子下
        3 = 梯子上
        4 = 梯子下
    """
    cls_name = cls_name.strip()
    mapping = {
        # 怪物 -> 0
        "火野猪": 0, "黑斧木妖": 0, "蘑菇仔": 0, "绿蜗牛": 0, "蓝蜗牛": 0,
        "红蜗牛": 0, "绿水灵": 0, "花蘑菇": 0, "木妖": 0, "绿蘑菇": 0,
        # 绳子 -> 1/2
        "绳子上": 1, "绳子下": 2, "rope_top": 1, "rope_bottom": 2,
        # 梯子 -> 3/4
        "梯子上": 3, "梯子下": 4, "ladder_top": 3, "ladder_bottom": 4,
    }
    # 未映射的默认为怪物（class 0）
    return mapping.get(cls_name, 0)


def label_images(images: list[Path], models: list[tuple[str, "YOLO", dict[int, str]]],
                 output_dir: Path) -> dict:
    """用多模型推理并保存标注。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"total": len(images), "labeled": 0, "boxes": 0}

    # 从模型元组中提取类别名
    model_names_cache: dict[str, dict[int, str]] = {}
    for name, yolo, names in models:
        model_names_cache[name] = names

    for i, img_path in enumerate(images):
        all_boxes: list[tuple[float, float, float, float, float, int]] = []

        for model_name, yolo, _names in models:
            results = yolo(str(img_path), conf=CONF, iou=IOU, verbose=False)
            class_names = model_names_cache.get(model_name, {})

            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes:
                    cls_id = int(box.cls)
                    cls_name = class_names.get(cls_id, f"cls_{cls_id}")
                    new_id = map_class(cls_name)
                    if new_id is None:
                        continue
                    conf = float(box.conf)
                    xyxy = box.xyxy.tolist()[0]
                    x1, y1, x2, y2 = xyxy[0], xyxy[1], xyxy[2], xyxy[3]
                    all_boxes.append((x1, y1, x2, y2, conf, new_id))

        if not all_boxes:
            (output_dir / f"{img_path.stem}.txt").write_text("", encoding="utf-8")
            continue

        # 简单去重: 同类别 + 重叠 > 0.5 合并
        all_boxes.sort(key=lambda b: b[0])
        merged: list[tuple[float, float, float, float, int]] = []
        for x1, y1, x2, y2, conf, cls_id in all_boxes:
            keep = True
            for mx1, my1, mx2, my2, mcls in merged:
                if cls_id != mcls:
                    continue
                ix1, iy1 = max(x1, mx1), max(y1, my1)
                # 计算 IoU
                ix1, iy1 = max(x1, mx1), max(y1, my1)
                ix2, iy2 = min(x2, mx2), min(y2, my2)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                inter = (ix2 - ix1) * (iy2 - iy1)
                area_a = (x2 - x1) * (y2 - y1)
                area_b = (mx2 - mx1) * (my2 - my1)
                iou_val = inter / (area_a + area_b - inter + 1e-6)
                if iou_val > 0.5:
                    keep = False
                    break
            if keep:
                merged.append((x1, y1, x2, y2, cls_id))

        # 写入 YOLO 格式
        lines = []
        w_img, h_img = float(TARGET_W), float(TARGET_H)
        for x1, y1, x2, y2, cls_id in merged:
            cx = (x1 + x2) / 2 / w_img
            cy = (y1 + y2) / 2 / h_img
            bw = (x2 - x1) / w_img
            bh = (y2 - y1) / h_img
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))
            bw = max(0.0001, min(1.0, bw))
            bh = max(0.0001, min(1.0, bh))
            lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        (output_dir / f"{img_path.stem}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

        stats["boxes"] += len(merged)
        stats["labeled"] += 1

        if (i + 1) % 20 == 0 or i == len(images) - 1:
            _log(f"  进度: {i+1}/{len(images)}  (标注 {stats['boxes']} 框)")

    return stats


def main():
    t_start = time.time()
    parser = argparse.ArgumentParser(description="YOLO 多模型自动标注")
    parser.add_argument("input", nargs="?", help="图片目录")
    parser.add_argument("output", nargs="?", help="标注输出目录")
    parser.add_argument("--device", default="auto",
                        help="设备: auto / cpu / cuda:0 / dml:0")
    parser.add_argument("--conf", type=float, default=CONF, help="置信度阈值")
    parser.add_argument("--iou", type=float, default=IOU, help="IoU 阈值")
    parser.add_argument("--model-file", default="", help="指定模型 pt 文件路径（覆盖自动选择）")
    args = parser.parse_args()

    input_dir = Path(args.input) if args.input else DEFAULT_INPUT
    output_dir = Path(args.output) if args.output else DEFAULT_OUTPUT

    images = sorted([f for f in input_dir.iterdir()
                     if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
    if not images:
        _log("图片池为空，退出")
        return

    gpu = gpu_utils.detect_gpu()
    _log(f"=== YOLO 多模型自动标注 ===")
    _log(f"图片: {len(images)} 张")
    _log(f"置信度: {args.conf}")
    _log(f"设备: {args.device} → {gpu_utils.resolve_device(args.device)}")
    _log(f"GPU: {gpu_utils.get_gpu_status_text()}")

    _log("开始加载已有模型...")
    models = load_models(MODELS_ROOT, device=args.device, model_file=args.model_file)
    if not models:
        _log("错误: trained_models/ 目录下没有 .pt 模型")
        _log("请把训练好的模型放到 trained_models/ 目录下")
        return

    model_name = models[0][0] if models else "未知"
    _log(f"模型: {model_name} (仅使用最新)")
    _log(f"开始标注 {len(images)} 张图片...")
    stats = label_images(images, models, output_dir)

    total_time = time.time() - t_start
    _log(f"========== 标注完成 ==========")
    _log(f"模型: {model_name}")
    _log(f"总耗时: {total_time:.1f}s")
    _log(f"图片: {stats['total']} 张")
    _log(f"有检出: {stats['labeled']} 张")
    _log(f"总框数: {stats['boxes']} 个")
    _log(f"无检出: {stats['total'] - stats['labeled']} 张")
    _log(f"标注文件: {output_dir}/")


if __name__ == "__main__":
    main()
