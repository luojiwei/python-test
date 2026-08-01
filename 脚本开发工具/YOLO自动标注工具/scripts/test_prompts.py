"""test_prompts.py — 测试不同提示词效果"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from transformers import pipeline
from PIL import Image
from pathlib import Path

pipe = pipeline("zero-shot-object-detection",
                model="IDEA-Research/grounding-dino-tiny", device="cpu")

sd = Path(r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\脚本开发工具\YOLO自动标注工具\screenshots")
imgs = sorted([f for f in sd.iterdir() if f.suffix == ".png"])[:3]

prompts = [
    "pixel art monster . 2d game enemy . sprite",
    "a cartoon creature in a video game . a small pixel art character",
    "monster . enemy character . non player character",
]

for img_path in imgs:
    img = Image.open(str(img_path)).convert("RGB")
    print(f"\n=== {img_path.name} ({img.size}) ===")
    for prompt in prompts:
        labels = [x.strip() for x in prompt.split(" . ")]
        results = pipe(img, candidate_labels=labels, threshold=0.15)
        if results:
            for r in results:
                box = r["box"]
                print(f"  [{r['score']:.3f}] {r['label']} @ "
                      f"({box['xmin']:.0f},{box['ymin']:.0f})-({box['xmax']:.0f},{box['ymax']:.0f})")
        else:
            print(f"  (empty) prompt={prompt[:40]}...")
