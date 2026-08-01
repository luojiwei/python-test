"""yolo_labeler.py — 用已有 YOLO 模型批量自动标注

读取 trained_models/ 下的所有 .pt 模型，逐个推理，合并结果，输出 YOLO 标注。
所有怪物类统一映射为 class_id=0。
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import sys
from pathlib import Path
from ultralytics import YOLO
from PIL import Image


MODELS_ROOT = Path(__file__).resolve().parent.parent / "trained_models"
DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "screenshots"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "dataset" / "labels" / "train"

TARGET_W, TARGET_H = 1280, 720
CONF = 0.3
IOU = 0.5


def load_models(models_root: Path) -> list[tuple[str, YOLO]]:
    """加载所有可用的训练模型。返回 [(名称, YOLO), ...]"""
    models = []
    if not models_root.exists():
        return models
    for pt_file in sorted(models_root.rglob("*.pt")):
        model_name = pt_file.parent.name if pt_file.parent != models_root else pt_file.stem
        try:
            yolo = YOLO(str(pt_file))
            models.append((model_name, yolo))
            print(f"  加载: {model_name} ({pt_file.name})")
        except Exception as e:
            print(f"  [跳过] {pt_file}: {e}")
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


def label_images(images: list[Path], models: list[tuple[str, YOLO]],
                 output_dir: Path) -> dict:
    """用多模型推理并保存标注。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"total": len(images), "labeled": 0, "boxes": 0}

    # 每个模型的类别名缓存
    model_names_cache: dict[str, dict[int, str]] = {}
    for name, yolo in models:
        try:
            model_names_cache[name] = yolo.model.names
        except Exception:
            model_names_cache[name] = {}

    for i, img_path in enumerate(images):
        all_boxes: list[tuple[float, float, float, float, float, int]] = []

        for model_name, yolo in models:
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
            print(f"  进度: {i+1}/{len(images)}  (标注 {stats['boxes']} 框)")

    return stats


def main():
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    images = sorted([f for f in input_dir.iterdir()
                     if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
    if not images:
        print("图片池为空")
        return

    print(f"=== YOLO 多模型自动标注 ===")
    print(f"图片: {len(images)} 张")
    print(f"置信度: {CONF}")

    print("\n加载已有模型:")
    models = load_models(MODELS_ROOT)
    if not models:
        print("错误: trained_models/ 目录下没有 .pt 模型")
        print("请把训练好的模型放到 trained_models/ 目录下")
        return

    print(f"\n开始标注...")
    stats = label_images(images, models, output_dir)

    print(f"\n完成!")
    print(f"  图片: {stats['total']} 张")
    print(f"  有检出: {stats['labeled']} 张")
    print(f"  总框数: {stats['boxes']} 个")
    print(f"  无检出: {stats['total'] - stats['labeled']} 张")
    print(f"  标注文件: {output_dir}/")


if __name__ == "__main__":
    main()
