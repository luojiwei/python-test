"""perception.py — 感知：模板匹配、YOLO检测、小地图定位、游戏状态"""

from dataclasses import dataclass, field

import cv2
import numpy as np

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
    注意：如果角色同时落在某个平台上（底部/顶部绳梯端点），不算在绳梯上。"""
    if wm is None:
        return False
    # 先检查是否在平台上——在平台上就不算绳梯
    on_platform = wm.find_platform(px, py) is not None
    for edge in wm.edges:
        if edge.get("type") != "rope":
            continue
        top = edge.get("top", {})
        bottom = edge.get("bottom", {})
        rope_x: float = float(top.get("x", 9999))
        rope_top_y: float = float(top.get("y", 9999))
        rope_bot_y: float = float(bottom.get("y", 9999))
        y_min = min(rope_top_y, rope_bot_y)
        y_max = max(rope_top_y, rope_bot_y)
        if abs(px - rope_x) <= x_tolerance and y_min <= py <= y_max:
            # 绳梯命中，但如果在平台上（绳梯端点附近）则不算
            if not on_platform:
                return True
    return False
