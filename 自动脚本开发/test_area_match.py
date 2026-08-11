"""用模板匹配用户框选的角色名位置，测试哪种方法最好"""
import cv2, numpy as np, json
from pathlib import Path

pp = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/test_output/snap_for_mark.png")
img = cv2.imdecode(np.frombuffer(pp.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
h, w = img.shape[:2]

# 角色名位置（用户框选）
cx1, cy1, cx2, cy2 = 1217, 407, 1295, 429

# 模板（底部UI）
ss = json.loads(Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/maps/system_setting.json").read_text(encoding="utf-8"))
e = ss["冒险岛怀旧服"]
tx1, ty1, tx2, ty2 = e["template_rect"]
template = img[ty1:ty2, tx1:tx2]
print(f"模板: {template.shape[1]}x{template.shape[0]}")

# 角色名区域（带点边距）
pad = 20
ry1 = max(0, cy1 - pad)
ry2 = min(h, cy2 + pad)
rx1 = max(0, cx1 - pad)
rx2 = min(w, cx2 + pad)
roi = img[ry1:ry2, rx1:rx2]
print(f"角色名ROI: ({rx1},{ry1})-({rx2},{ry2}) {roi.shape[1]}x{roi.shape[0]}")

tg = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
rg = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

methods = {}
for name, method in [("CCOEFF", cv2.TM_CCOEFF_NORMED), ("CCORR", cv2.TM_CCORR_NORMED)]:
    # raw
    r = cv2.matchTemplate(rg, tg, method)
    _, v, _, _ = cv2.minMaxLoc(r)
    methods[f"{name}(raw)"] = v
    # otsu
    _, t_ot = cv2.threshold(tg, 0, 255, cv2.THRESH_BINARY|cv2.THRESH_OTSU)
    _, r_ot = cv2.threshold(rg, 0, 255, cv2.THRESH_BINARY|cv2.THRESH_OTSU)
    r = cv2.matchTemplate(r_ot, t_ot, method)
    _, v, _, _ = cv2.minMaxLoc(r)
    methods[f"{name}(otsu)"] = v
    # bin 127
    _, t_b = cv2.threshold(tg, 127, 255, cv2.THRESH_BINARY)
    _, r_b = cv2.threshold(rg, 127, 255, cv2.THRESH_BINARY)
    r = cv2.matchTemplate(r_b, t_b, method)
    _, v, _, _ = cv2.minMaxLoc(r)
    methods[f"{name}(bin)"] = v
    # edge
    t_e = cv2.Canny(tg, 50, 150)
    r_e = cv2.Canny(rg, 50, 150)
    r = cv2.matchTemplate(r_e, t_e, method)
    _, v, _, _ = cv2.minMaxLoc(r)
    methods[f"{name}(edge)"] = v

print("\n各方法在该区域匹配度:")
for k, v in sorted(methods.items(), key=lambda x: -x[1]):
    print(f"  {k:18s} {v:.4f} {'!' if v > 0.7 else ''}")

# 标注
out = img.copy()
cv2.rectangle(out, (cx1, cy1), (cx2, cy2), (0, 0, 255), 2)
cv2.rectangle(out, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
cv2.putText(out, f"role ({cx1},{cy1})-({cx2},{cy2})", (cx1, cy1-5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

pp_out = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/test_output/area_match.png")
pp_out.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp_out.write_bytes(buf.tobytes())
print(f"\n已保存: {pp_out}")
