"""标记这张截图上的角色名位置"""
import cv2, numpy as np
from pathlib import Path
import json

# 读这张截图
src = Path("C:/Users/Administrator/.workbuddy/clipboard-images/clipboard-2026-08-08T06-15-04-673Z-48402e7e.jpg")
img = cv2.imread(str(src))
h, w = img.shape[:2]
print(f"截图: {w}x{h}")

# 读配置（用模板）
ss_path = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/maps/system_setting.json")
ss = json.loads(ss_path.read_text(encoding="utf-8"))
title = "冒险岛怀旧服"
e = ss.get(title, {})
tx1, ty1, tx2, ty2 = e["template_rect"]
sr = e.get("search_region", [0, 0, w, int(h * 0.9)])

print(f"模板区域: ({tx1},{ty1})-({tx2},{ty2}) {tx2-tx1}x{ty2-ty1}")
print(f"搜索范围: {sr}")

# 但这是 910x540 截图，配置是 1382x807 的——尺寸不匹配！
# 按比例缩放
sx = w / 1382
sy = h / 807
print(f"缩放比: x={sx:.3f} y={sy:.3f}")

# 缩放坐标
scaled_template = (int(tx1*sx), int(ty1*sy), int(tx2*sx), int(ty2*sy))
scaled_sr = (int(sr[0]*sx), int(sr[1]*sy), int(sr[2]*sx), int(sr[3]*sy))
print(f"缩放后模板: {scaled_template}")
print(f"缩放后搜索范围: {scaled_sr}")

# 提取缩放后模板
tx1s, ty1s, tx2s, ty2s = scaled_template
template = img[ty1s:ty2s, tx1s:tx2s]
print(f"模板大小: {template.shape[1]}x{template.shape[0]}")

# 在缩放后的搜索范围内匹配
sx1, sy1, sx2, sy2 = scaled_sr
roi = img[sy1:sy2, sx1:sx2]
print(f"ROI: {roi.shape[1]}x{roi.shape[0]}")

# 因为模板分辨率变了，缩放匹配
resized_template = cv2.resize(template, None, fx=sx, fy=sy, interpolation=cv2.INTER_AREA)
print(f"缩放后模板大小: {resized_template.shape[1]}x{resized_template.shape[0]}")

if roi.shape[0] < resized_template.shape[0] or roi.shape[1] < resized_template.shape[1]:
    print("ROI 小于模板！无法匹配")
else:
    result = cv2.matchTemplate(roi, resized_template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, ml = cv2.minMaxLoc(result)
    cx = sx1 + ml[0] + resized_template.shape[1] // 2
    cy = sy1 + ml[1] + resized_template.shape[0] // 2
    print(f"最佳匹配: conf={max_val:.3f} pos=({cx},{cy})")

    # 标注
    out = img.copy()
    # 搜索范围
    cv2.rectangle(out, (sx1, sy1), (sx2, sy2), (255, 0, 0), 2)
    # 匹配位置
    cv2.circle(out, (cx, cy), 14, (0, 0, 255), 3)
    cv2.putText(out, f"({cx},{cy}) conf={max_val:.2f}", (cx + 16, cy),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    # 模板位置
    cv2.rectangle(out, (tx1s, ty1s), (tx2s, ty2s), (0, 255, 0), 2)

    pp = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/test_output/marked.png")
    pp.parent.mkdir(exist_ok=True)
    _, buf = cv2.imencode(".png", out)
    pp.write_bytes(buf.tobytes())
    print(f"已保存: {pp}")