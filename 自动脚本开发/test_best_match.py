"""尝试多种匹配策略"""
import ctypes, ctypes.wintypes, cv2, numpy as np
from pathlib import Path

class _BIH(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]

import json
ss = json.loads(Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/maps/system_setting.json").read_text(encoding="utf-8"))
e = ss.get("冒险岛怀旧服", {})
tx1, ty1, tx2, ty2 = e["template_rect"]
sr = e.get("search_region", [4, 67, 1368, 700])

# 用 debug_frames 里的最新一张图
df = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/debug_frames")
imgs = sorted([f for f in df.glob("frame_*.png") if f.stat().st_size > 50000])

def load(p):
    return cv2.imdecode(np.frombuffer(p.read_bytes(), np.uint8), cv2.IMREAD_COLOR)

frame = load(imgs[-1])  # 最新帧（frame_49）
h, w = frame.shape[:2]
print(f"截图: {w}x{h}")

# 原始模板
template = frame[ty1:ty2, tx1:tx2]
th, tw = template.shape[:2]

# 搜索 ROI
sx1, sy1, sx2, sy2 = sr
roi = frame[sy1:sy2, sx1:sx2]

methods = {}

# 方法1: 原始
result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(raw)"] = (v, p)

# 方法2: 模板二值化（白字黑底）
_, t_bin = cv2.threshold(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), 127, 255, cv2.THRESH_BINARY)
_, r_bin = cv2.threshold(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 127, 255, cv2.THRESH_BINARY)
result = cv2.matchTemplate(r_bin, t_bin, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(bin127)"] = (v, p)

# 方法3: 自适应二值化
t_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
r_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
t_ad = cv2.adaptiveThreshold(t_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
r_ad = cv2.adaptiveThreshold(r_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
result = cv2.matchTemplate(r_ad, t_ad, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(adap)"] = (v, p)

# 方法4: 模板 Otsu 二值化
_, t_ot = cv2.threshold(t_gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
_, r_ot = cv2.threshold(r_gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
result = cv2.matchTemplate(r_ot, t_ot, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(otsu)"] = (v, p)

# 方法5: Canny 边缘匹配
t_c = cv2.Canny(t_gray, 50, 150)
r_c = cv2.Canny(r_gray, 50, 150)
result = cv2.matchTemplate(r_c, t_c, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(canny)"] = (v, p)

# 方法6: 取反（黑底白字 -> 白底黑字）
t_inv = 255 - t_bin
r_inv = 255 - cv2.threshold(r_gray, 127, 255, cv2.THRESH_BINARY)[1]
result = cv2.matchTemplate(r_inv, t_inv, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(inv)"] = (v, p)

# 方法7: TM_SQDIFF_NORMED (越小越好)
result = cv2.matchTemplate(r_ot, t_ot, cv2.TM_SQDIFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
# SQDIFF 最小值才是最佳匹配
min_v, _, min_p, _ = cv2.minMaxLoc(result)
methods["SQDIFF(otsu)"] = (1.0 - min_v, min_p)

# 方法8: 白字检测 - 只保留白色像素
t_mask = (t_gray > 180).astype(np.uint8) * 255
r_mask = (r_gray > 180).astype(np.uint8) * 255
result = cv2.matchTemplate(r_mask, t_mask, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(mask>180)"] = (v, p)

# 方法9: 白字检测加膨胀（让笔画更粗）
kernel = np.ones((2, 2), np.uint8)
t_mask2 = cv2.dilate(t_mask, kernel)
r_mask2 = cv2.dilate(r_mask, kernel)
result = cv2.matchTemplate(r_mask2, t_mask2, cv2.TM_CCOEFF_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCOEFF(mask+dil)"] = (v, p)

# 方法10: TM_CCORR_NORMED — 对整体亮度不敏感
result = cv2.matchTemplate(roi, template, cv2.TM_CCORR_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCORR(raw)"] = (v, p)

# 方法11: TM_CCORR_NORMED + Otsu 二值化
result = cv2.matchTemplate(r_ot, t_ot, cv2.TM_CCORR_NORMED)
_, v, _, p = cv2.minMaxLoc(result)
methods["CCORR(otsu)"] = (v, p)

# 输出结果
print("\n各方法匹配结果:")
for name, (conf, pos) in sorted(methods.items(), key=lambda x: -x[1][0]):
    cx = sx1 + pos[0] + tw // 2
    cy = sy1 + pos[1] + th // 2
    print(f"  {name:20s} conf={conf:.3f} pos=({cx},{cy})")

# 用最佳方法标注
best = max(methods, key=lambda k: methods[k][0])
print(f"\n最佳方法: {best} conf={methods[best][0]:.3f}")

best_conf, best_pos = methods[best]
out = frame.copy()
cv2.rectangle(out, (sx1, sy1), (sx2, sy2), (255, 0, 0), 2)
best_cx = sx1 + best_pos[0] + tw // 2
best_cy = sy1 + best_pos[1] + th // 2
cv2.circle(out, (best_cx, best_cy), 14, (0, 255, 0), 3)
cv2.putText(out, f"{best} {best_conf:.2f}", (best_cx + 15, best_cy),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

pp = Path("F:/Program Files/WorkBuddy Works/python-test/自动脚本开发/test_output/best_match.png")
pp.parent.mkdir(exist_ok=True)
_, buf = cv2.imencode(".png", out)
pp.write_bytes(buf.tobytes())
print(f"已保存: {pp}")
