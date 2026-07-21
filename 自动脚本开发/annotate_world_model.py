#!/usr/bin/env python3
"""
在世界模型基础上，把平台图绘制到 魔法密林南郊_ropes.png 上。
小地图坐标 (0..112, 0..135) → 图像像素坐标 (0..580, 0..700)
"""

import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---------- 加载数据 ----------

MAPS_PATH = r"D:\Program Files (x86)\Tencent\WorkBuddy\脚本开发工具\地图标记工具\marker_output\maps.json"
WORLD_PATH = r"D:\Program Files (x86)\Tencent\WorkBuddy\自动脚本开发\world_model.json"
IMG_PATH = r"D:\Program Files (x86)\Tencent\WorkBuddy\脚本开发工具\地图标记工具\marker_output\魔法密林南郊_ropes.png"
OUT_PATH = r"D:\Program Files (x86)\Tencent\WorkBuddy\自动脚本开发\world_model_overlay.png"

with open(MAPS_PATH, encoding="utf-8") as f:
    raw_maps = json.load(f)
with open(WORLD_PATH, encoding="utf-8") as f:
    world = json.load(f)

map_data = list(raw_maps.values())[0]
platforms = world["platforms"]
edges = world["edges"]
mm_size = map_data["minimap_size"]    # [112, 135]
mm_w, mm_h = mm_size

# ---------- 坐标映射 ----------

img = Image.open(IMG_PATH).convert("RGBA")
W, H = img.size
scale_x = W / mm_w
scale_y = H / mm_h

print(f"图片: {W}x{H}, 小地图: {mm_w}x{mm_h}, scale=({scale_x:.2f}, {scale_y:.2f})")

def to_px(x: float, y: float) -> tuple[int, int]:
    return int(x * scale_x), int(y * scale_y)


# ---------- 创建叠加层 ----------

overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)

# 字体：尝试加载，找不到用默认
def load_font(size: int):
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

font_label = load_font(22)
font_small = load_font(16)
font_legend = load_font(18)


# ---------- 1. 绘制平台轮廓 ----------

PLATFORM_COLOR = (80, 200, 255, 140)        # 青色
PLATFORM_BORDER = (255, 255, 255, 230)

for p in platforms:
    pid = p["id"]

    # 用 all_points 画平台形状
    pts = p.get("all_points", [])
    if not pts:
        continue
    poly = [to_px(x, y) for x, y in pts]

    color = PLATFORM_COLOR
    border = PLATFORM_BORDER

    # 填充多边形
    draw.polygon(poly, fill=color, outline=border)

    # 在平台中心标 ID
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    px, py = to_px(cx, cy)
    label = pid.replace("platform_", "P")
    bbox = draw.textbbox((0, 0), label, font=font_label)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle(
        [px - tw // 2 - 4, py - th // 2 - 2, px + tw // 2 + 4, py + th // 2 + 2],
        fill=(0, 0, 0, 200),
    )
    text_color = (255, 255, 255, 255)
    draw.text((px - tw // 2, py - th // 2 - 2), label,
              fill=text_color, font=font_label)


# ---------- 2. 绘制绳梯（黄绿线）----------

ROPE_COLOR = (255, 220, 40, 220)    # 黄色
for e in edges:
    if e["type"] != "rope":
        continue
    top = e["top"]
    bot = e["bottom"]
    p1 = to_px(top["x"], top["y"])
    p2 = to_px(bot["x"], bot["y"])
    draw.line([p1, p2], fill=ROPE_COLOR, width=4)
    # 端点画小圆
    draw.ellipse([p1[0] - 6, p1[1] - 6, p1[0] + 6, p1[1] + 6],
                 fill=(255, 255, 100, 255), outline=(0, 0, 0, 200), width=2)
    draw.ellipse([p2[0] - 6, p2[1] - 6, p2[0] + 6, p2[1] + 6],
                 fill=(255, 255, 100, 255), outline=(0, 0, 0, 200), width=2)


# ---------- 3. 绘制跳跃点（紫红虚线）----------

JUMP_COLOR = (220, 80, 220, 240)
FLASH_COLOR = (255, 120, 40, 240)
for e in edges:
    etype = e["type"]
    if etype not in ("jump", "flash"):
        continue
    color = FLASH_COLOR if etype == "flash" else JUMP_COLOR
    p1 = to_px(e["from"]["x"], e["from"]["y"])
    p2 = to_px(e["to"]["x"], e["to"]["y"])
    # 虚线（jump）/ 实线（flash）
    if etype == "jump":
        dash_len = 10
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        dist = max(1, ((dx) ** 2 + (dy) ** 2) ** 0.5)
        steps = int(dist / (dash_len * 2))
        for i in range(steps + 1):
            t1 = (i * 2 * dash_len) / dist
            t2 = min(((i * 2 + 1) * dash_len) / dist, 1.0)
            s = (int(p1[0] + dx * t1), int(p1[1] + dy * t1))
            en = (int(p1[0] + dx * t2), int(p1[1] + dy * t2))
            draw.line([s, en], fill=color, width=4)
    else:
        draw.line([p1, p2], fill=color, width=4)
    # 端点画方块
    for p in [p1, p2]:
        draw.rectangle([p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5],
                       fill=color, outline=(255, 255, 255, 220), width=1)


# ---------- 4. 图例 ----------

legend_x, legend_y = 10, 10
line_h = 30
items = [
    ("黄色实线 + 圆点 = rope", "rope", (255, 220, 40, 255)),
    ("紫红虚线 + 方块 = jump", "jump", (220, 80, 220, 255)),
    ("橙色实线 + 方块 = flash", "flash", (255, 120, 40, 255)),
    ("青色多边形 = 平台", "platform", (80, 200, 255, 200)),
    ("P0~P11 = 平台 ID", "label", (255, 255, 255, 255)),
]

# 背景框
draw.rectangle([legend_x, legend_y, legend_x + 320, legend_y + line_h * len(items) + 15],
               fill=(0, 0, 0, 200), outline=(255, 255, 255, 200), width=1)

for i, (text, kind, color) in enumerate(items):
    y = legend_y + 8 + i * line_h
    if kind == "rope":
        draw.line([(legend_x + 12, y + 12), (legend_x + 40, y + 12)],
                  fill=color, width=4)
        draw.ellipse([legend_x + 22, y + 6, legend_x + 32, y + 16],
                     fill=(255, 255, 100, 255), outline=(0, 0, 0, 255))
    elif kind == "jump":
        for j in range(2):
            x1 = legend_x + 12 + j * 14
            x2 = x1 + 10
            draw.line([(x1, y + 12), (x2, y + 12)], fill=color, width=4)
    elif kind == "flash":
        draw.line([(legend_x + 12, y + 12), (legend_x + 40, y + 12)],
                  fill=color, width=4)
    elif kind == "platform":
        draw.rectangle([legend_x + 12, y + 5, legend_x + 40, y + 20],
                       fill=color, outline=(255, 255, 255, 255), width=1)
    elif kind == "label":
        draw.text((legend_x + 14, y + 2), "P0", fill=color, font=font_small)

    draw.text((legend_x + 55, y), text, fill=(255, 255, 255, 255), font=font_small)


# ---------- 5. 状态摘要 ----------

# 检测孤立平台（没有任何边的节点）
has_edge: set[str] = set()
for e in edges:
    has_edge.add(e["from_platform"])
iso_ids = sorted([p["id"] for p in platforms if p["id"] not in has_edge])
if iso_ids:
    for i, pid in enumerate(iso_ids):
        y = H - 30 + i * 24
        draw.rectangle([10, y - 4, 200, y + 22],
                       fill=(255, 80, 80, 220), outline=(255, 255, 255, 255), width=1)
        draw.text((14, y), f"⚠ {pid} - 孤立平台", fill=(255, 255, 255, 255), font=font_legend)
else:
    draw.text((14, H - 30), "✓ 全部 12 平台连通", fill=(80, 255, 80, 255), font=font_legend)


# ---------- 合成 + 输出 ----------

result = Image.alpha_composite(img, overlay)
result.convert("RGB").save(OUT_PATH, "PNG")
print(f"✓ 已保存: {OUT_PATH}")
print(f"  {len(platforms)} 平台, {len(edges)} 边")
