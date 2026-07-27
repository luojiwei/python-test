"""perception.py — 感知：模板匹配、YOLO检测、小地图定位、游戏状态"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from edge_types import EdgeType

import config
from config import (
    YOLO_CONF, YOLO_IOU,
    MATCH_THRESHOLD, SEARCH_BOTTOM_SKIP_PCT,
    DOT_HSV_LOWER, DOT_HSV_UPPER,
)

# ============================================================
# 模板匹配 — 角色定位
# ============================================================

def find_character(frame_bgr: np.ndarray, template_bgr: np.ndarray,
                   search_region: tuple[int, int, int, int]) -> tuple[int, int, float] | None:
    sx1, sy1, sx2, sy2 = search_region
    th, tw = template_bgr.shape[:2]
    roi = frame_bgr[sy1:sy2, sx1:sx2]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return None
    result = cv2.matchTemplate(roi, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < MATCH_THRESHOLD:
        return None
    cx = sx1 + max_loc[0] + tw // 2
    cy = sy1 + max_loc[1] + th // 2
    return cx, cy, max_val


# ============================================================
# YOLO 怪物检测
# ============================================================

def detect_monsters(model, frame_bgr: np.ndarray) -> list[dict]:
    """YOLO推理 → 只返回怪物"""
    results = model.predict(frame_bgr, conf=YOLO_CONF, iou=YOLO_IOU,
                            device="cpu", verbose=False)
    monsters: list[dict] = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            cls_id = int(box.cls)
            cls_name = config.CLASS_NAMES.get(cls_id, "")
            if cls_name in config.NON_MONSTER_NAMES:
                continue
            conf = float(box.conf)
            xyxy = box.xyxy.tolist()[0]
            x1, y1, x2, y2 = xyxy
            monsters.append({
                "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "conf": conf, "cls": cls_id,
            })
    return monsters


# ============================================================
# 小地图黄点定位
# ============================================================

def find_yellow_dot(mm_bgr: np.ndarray) -> tuple[float, float] | None:
    """在小地图截图中找角色黄点，返回 (x, y) 或 None"""
    hsv = cv2.cvtColor(mm_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, DOT_HSV_LOWER, DOT_HSV_UPPER)
    if mask.sum() < 5:
        return None
    num, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num <= 1:
        return None
    best = None
    best_sv = 0.0
    for i in range(1, num):
        area = stats[i, cv2.CC_STAT_AREA]
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if 2 <= area <= 50 and max(w, h) / max(1, min(w, h)) <= 1.8:
            cx, cy = centroids[i]
            ys, xs = np.where(labels == i)
            pixels = hsv[ys, xs]
            sv = float(np.mean(pixels[:, 1])) * float(np.mean(pixels[:, 2]))
            if sv > best_sv:
                best_sv = sv
                best = (float(cx), float(cy))
    return best


# ============================================================
# 游戏状态
# ============================================================

@dataclass
class GameState:
    player_screen_x: float = 0
    player_screen_y: float = 0
    player_minimap_x: float = 0
    player_minimap_y: float = 0
    current_platform: str | None = None
    monsters: list[dict] = field(default_factory=list)
    facing: str = "r"
    timestamp: float = 0.0
    on_rope: bool = False           # 当前是否在绳梯上
    rope_frames: int = 0            # 连续在绳梯上的帧数


# ============================================================
# 绳梯检测
# ============================================================

def detect_on_rope(wm, px: float, py: float, x_tolerance: int = 5) -> bool:
    """检测小地图坐标 (px, py) 是否落在任意绳梯范围内。
    距绳梯端点 >3px → 算绳梯；距端点 ≤3px 且在平台上 → 不算（站在平台地面）。"""
    if wm is None:
        return False
    for edge in wm.edges:
        if edge.get("type") != EdgeType.ROPE:
            continue
        top = edge.get("top", {})
        bottom = edge.get("bottom", {})
        rope_x: float = float(top.get("x", 9999))
        rope_top_y: float = float(top.get("y", 9999))
        rope_bot_y: float = float(bottom.get("y", 9999))
        y_min = min(rope_top_y, rope_bot_y)
        y_max = max(rope_top_y, rope_bot_y)
        if abs(px - rope_x) <= x_tolerance and y_min <= py <= y_max:
            # 距绳子两端 3px 以内且在平台上 → 不算绳梯（站在平台地面）
            if min(abs(py - y_min), abs(py - y_max)) <= 3:
                if wm.find_platform(px, py) is not None:
                    continue
            return True
    return False


# ============================================================
# 运行时自校准：小地图 ↔ 屏幕坐标映射
# ============================================================

class Calibrator:
    """利用模板匹配成功时的 (mm, screen) 配对数据，训练线性回归。
    模板失败时用小地图推屏幕坐标，输出置信度。"""
    def __init__(self, max_samples: int = 50) -> None:
        self._max = max_samples
        self._cols: list[tuple[float, float, float, float]] = []  # (mm_x, mm_y, sc_x, sc_y)
        self._last_update: float = 0.0

    def add(self, mm_x: float, mm_y: float, sc_x: float, sc_y: float) -> None:
        import time
        self._cols.append((mm_x, mm_y, sc_x, sc_y))
        if len(self._cols) > self._max:
            self._cols.pop(0)
        self._last_update = time.time()

    def has_data(self) -> bool:
        return len(self._cols) >= 5

    def _linreg(self, xs: list[float], ys: list[float]) -> tuple[float, float, float]:
        """返回 (slope, intercept, r2)"""
        n = len(xs)
        if n < 2:
            return 1.0, 0.0, 0.0
        mx = sum(xs) / n
        my = sum(ys) / n
        ss_xy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        ss_xx = sum((x - mx) ** 2 for x in xs)
        ss_yy = sum((y - my) ** 2 for y in ys)
        if ss_xx < 1e-6:
            return 1.0, my, 0.0
        slope = ss_xy / ss_xx
        intercept = my - slope * mx
        r2 = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 1e-6 else 0.0
        return slope, intercept, max(0.0, min(1.0, r2))

    def predict(self, mm_x: float, mm_y: float) -> tuple[float, float, float]:
        """返回 (screen_x, screen_y, confidence)"""
        import time
        if not self.has_data():
            return 0.0, 0.0, 0.0

        xs_mm = [c[0] for c in self._cols]
        ys_mm = [c[1] for c in self._cols]
        xs_sc = [c[2] for c in self._cols]
        ys_sc = [c[3] for c in self._cols]

        sx, ox, r2_x = self._linreg(xs_mm, xs_sc)
        sy, oy, r2_y = self._linreg(ys_mm, ys_sc)

        screen_x = sx * mm_x + ox
        screen_y = sy * mm_y + oy

        # 置信度 = R²均值 × 时效衰减
        r2_avg = (r2_x + r2_y) / 2
        age = time.time() - self._last_update
        decay = max(0.0, 1.0 - age / 30.0)  # 30s 线性衰减到 0
        conf = r2_avg * decay

        # R²过低 → 置信度打折
        if r2_avg < 0.5:
            conf *= (r2_avg / 0.5)

        return screen_x, screen_y, conf
