"""gd_label.py — Grounding DINO 批量自动标注脚本（基于 transformers）

用法:
    python gd_label.py --input <图片目录> --output <标注输出目录>
        --prompt "monster . enemy" --conf 0.3

首次运行自动从镜像站下载模型 (~170MB)，需联网。
"""

import os
import argparse
import sys
from pathlib import Path

# ---- 国内 HuggingFace 镜像 ----
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def main():
    parser = argparse.ArgumentParser(description="Grounding DINO 批量自动标注")
    parser.add_argument("--input", required=True, help="图片目录")
    parser.add_argument("--output", required=True, help="标注输出目录 (YOLO .txt)")
    parser.add_argument("--prompt", default="monster . game enemy . creature",
                        help="检测提示词")
    parser.add_argument("--conf", type=float, default=0.3, help="置信度阈值")
    parser.add_argument("--model", default="IDEA-Research/grounding-dino-tiny",
                        help="HuggingFace 模型ID: base(~700M) 或 tiny(~170M)")
    parser.add_argument("--device", default="cpu", help="设备: cpu / cuda")
    parser.add_argument("--img-width", type=int, default=1280, help="图片宽度")
    parser.add_argument("--img-height", type=int, default=720, help="图片高度")

    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    images = sorted([f for f in input_dir.iterdir()
                     if f.suffix.lower() in (".png", ".jpg", ".jpeg")])
    if not images:
        print(f"错误: {input_dir} 中没有图片")
        sys.exit(1)

    print(f"Grounding DINO 标注")
    print(f"  图片: {len(images)} 张")
    print(f"  提示词: {args.prompt}")
    print(f"  置信度: {args.conf}")
    print(f"  模型: {args.model}")

    # ---- 加载模型 ----
    print("加载模型中 (首次需下载 ~170MB)...")
    from transformers import pipeline
    detector = pipeline(
        "zero-shot-object-detection",
        model=args.model,
        device=args.device,
    )
    print("模型就绪")

    # ---- 批量检测 ----
    total_boxes = 0
    empty_count = 0

    for i, img_path in enumerate(images):
        try:
            from PIL import Image
            img = Image.open(str(img_path)).convert("RGB")

            results = detector(img, candidate_labels=args.prompt.split(" . "),
                               threshold=args.conf)

            lines = []
            for det in results:
                x1, y1, x2, y2 = (det["box"]["xmin"], det["box"]["ymin"],
                                   det["box"]["xmax"], det["box"]["ymax"])
                # 转为 YOLO 归一化格式 (class_id=0 统一为怪物)
                w_img, h_img = args.img_width, args.img_height
                cx = (x1 + x2) / 2 / w_img
                cy = (y1 + y2) / 2 / h_img
                bw = (x2 - x1) / w_img
                bh = (y2 - y1) / h_img
                cx = max(0.0, min(1.0, cx))
                cy = max(0.0, min(1.0, cy))
                bw = max(0.0001, min(1.0, bw))
                bh = max(0.0001, min(1.0, bh))
                lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

            label_path = output_dir / f"{img_path.stem}.txt"
            label_path.write_text("\n".join(lines) + "\n" if lines else "",
                                  encoding="utf-8")
            total_boxes += len(lines)
            if not lines:
                empty_count += 1

            if (i + 1) % 10 == 0 or i == len(images) - 1:
                print(f"  进度: {i+1}/{len(images)}  (检出 {total_boxes} 框, 空 {empty_count} 张)")

        except Exception as e:
            print(f"  [警告] {img_path.name}: {e}")
            # 写入空标注避免重复处理
            (output_dir / f"{img_path.stem}.txt").write_text("", encoding="utf-8")

    # ---- 统计 ----
    print(f"\n完成!")
    print(f"  图片: {len(images)} 张")
    print(f"  检出框: {total_boxes} 个")
    print(f"  无检出: {empty_count} 张")
    print(f"  平均: {total_boxes / max(1, len(images)):.1f} 框/张")


if __name__ == "__main__":
    main()
