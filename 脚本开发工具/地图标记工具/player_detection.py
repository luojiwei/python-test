"""小地图玩家光点检测与追踪。

使用 HSV 颜色空间 + 亮度过滤来检测玩家黄色光点，
配合 PlayerTracker 实现多帧稳定追踪。
"""

import time

import cv2
import numpy as np

DOT_HSV_LOWER = np.array([18, 100, 150])
DOT_HSV_UPPER = np.array([40, 255, 255])


def _find_yellow_candidates(minimap_bgr):
    """检测玩家光点。

    关键特征: R+G 总亮度 >= 450 (光点≈510, 草地≈340-400, 区分明显)
    同时要求: 黄色调 (B<R, B<G), 中小面积 (3-80px)
    """
    bgr = minimap_bgr.astype(np.int32)
    R = bgr[:, :, 2]
    G = bgr[:, :, 1]
    B = bgr[:, :, 0]
    # 核心过滤: 亮度足够, 且为黄色调
    mask = ((R + G) >= 450) & (R > B) & (G > B) & (R >= 180) & (G >= 180)
    if mask.sum() < 3:
        return []
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    if num_labels <= 1:
        return []
    cands = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if 3 <= area <= 80:
            cx, cy = centroids[i]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            aspect = max(w, h) / max(1, min(w, h))
            if aspect <= 2.0:
                cands.append((int(cx), int(cy), area, float(R[int(cy), int(cx)] + G[int(cy), int(cx)])))
    return cands


class PlayerTracker:
    """多帧追踪器 — 根据候选位置持续追踪玩家光点。

    使用衰减计数机制，对静止帧施加计数上限以过滤误检。
    """

    def __init__(self, max_dist: int = 15, decay_per_sec: float = 0.15,
                 stationary_cap: float = 5.0):
        self.max_dist = max_dist
        self.decay_per_sec = decay_per_sec
        self.stationary_cap = stationary_cap
        self.tracks = {}  # {(x,y): {"count": float, "speed": float, "sv_score": float}}
        self.last_t = time.time()

    def update(self, candidates):
        """喂入候选位置列表，返回当前最可能的玩家坐标 (x, y) 或 None。"""
        now = time.time()
        dt = now - self.last_t
        self.last_t = now
        for k in list(self.tracks.keys()):
            self.tracks[k]["count"] -= self.decay_per_sec * dt
            if self.tracks[k]["count"] <= 0:
                del self.tracks[k]
        for cx, cy, area, sv in candidates:
            best_key = None
            best_dist = self.max_dist
            for k in list(self.tracks.keys()):
                d = ((cx - k[0]) ** 2 + (cy - k[1]) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_key = k
            if best_key is not None:
                ox, oy = best_key
                vx, vy = cx - ox, cy - oy
                speed = (vx * vx + vy * vy) ** 0.5
                prev_sv = self.tracks[best_key].get("sv_score", sv)
                new_sv = (prev_sv + sv) / 2
                new_count = self.tracks[best_key]["count"] + 1.0
                if speed < 1.5:
                    new_count = min(new_count, self.stationary_cap)
                self.tracks.pop(best_key)
                self.tracks[(cx, cy)] = {"count": new_count, "speed": speed, "sv_score": new_sv}
            else:
                self.tracks[(cx, cy)] = {"count": 1.0, "speed": 0.0, "sv_score": sv}
        if not self.tracks:
            return None

        def score(k):
            t = self.tracks[k]
            return t["count"] + t["speed"] * 0.5 + (t.get("sv_score", 0) / 100000)

        best = max(self.tracks.keys(), key=score)
        if self.tracks[best]["count"] >= 0.5:
            return best
        return None


def detect_player_dot(minimap_bgr, tracker: PlayerTracker):
    """检测玩家光点并更新追踪器。

    Args:
        minimap_bgr: BGR 格式的小地图 numpy 数组
        tracker: PlayerTracker 实例

    Returns:
        (x, y) 玩家坐标 或 None
    """
    cands = _find_yellow_candidates(minimap_bgr)
    return tracker.update(cands)
