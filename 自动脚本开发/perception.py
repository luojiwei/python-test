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
            if cls_id not in config.MONSTER_CLASSES:
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
