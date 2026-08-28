"""perception.py — 感知：YOLO检测、OCR角色定位、小地图定位、游戏状态"""

import threading
from dataclasses import dataclass, field
import logging

import cv2
import numpy as np
import torch

from edge_types import EdgeType

import config
from config import (
    YOLO_CONF, YOLO_IOU,
    DOT_HSV_LOWER, DOT_HSV_UPPER,
    MONSTER_CLASS_NAMES, PLAYER_CLASS_NAME,
    CHARACTER_NAME, PLAYER_NAME_ROI_ABOVE, PLAYER_NAME_ROI_BELOW,
    OCR_UPSCALE, OCR_NAME_MATCH_THRESHOLD,
)

_logger = logging.getLogger(__name__)

# ============================================================
# OCR 引擎（延迟加载，全局单例）
# ============================================================

_ocr_engine = None
_ocr_engine_lock = threading.Lock()


def _get_ocr_engine():
    """延迟加载 RapidOCR 引擎（首次调用初始化，之后复用）。"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_engine_lock:
            if _ocr_engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _ocr_engine = RapidOCR()
                except Exception as e:
                    _logger.warning(f"OCR 引擎加载失败: {e}，角色 OCR 识别不可用")
                    return None
    return _ocr_engine


def _ocr_text(img_bgr: np.ndarray) -> str:
    """对图像做 OCR，返回拼接后的文本（失败返回空串）。"""
    engine = _get_ocr_engine()
    if engine is None or img_bgr is None or img_bgr.size == 0:
        return ""
    try:
        result, _ = engine(img_bgr)
        if not result:
            return ""
        texts = [str(item[1]) for item in result if len(item) >= 2]
        return "".join(texts)
    except Exception as e:
        _logger.warning(f"OCR 识别异常: {e}")
        return ""


def _normalize_name(s: str) -> str:
    """昵称归一化：去掉空白与标点，保留中文/字母/数字，转小写。"""
    return "".join(ch for ch in s
                   if ch.isalnum() or '\u4e00' <= ch <= '\u9fff').lower()


def _name_match(ocr_text: str, target: str) -> float:
    """模糊匹配 OCR 文本与目标角色名，返回相似度 0~1。

    角色名被遮挡时 OCR 常只识别出部分/形近字符（如漏字、错字、混入
    其他文字），单一策略会漏判。这里组合多种策略取最大值：
      1) 完全相等 → 1.0
      2) 子串包含（OCR 漏字/多字，如 "小罗" ⊂ "爱吃姜的小罗"）→ 0.9
      3) LCS 最长公共子序列长度归一化（容忍前后混入干扰文字）
      4) SequenceMatcher 字符级相似度
      5) 字符集 Jaccard（容忍乱序/漏字，中文昵称较短时较稳健）
    """
    a = _normalize_name(ocr_text)
    b = _normalize_name(target)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:  # 包含关系：OCR 常漏字/多字
        return 0.9

    from difflib import SequenceMatcher
    sm = SequenceMatcher(None, a, b, autojunk=False)

    # 3) LCS 长度（跳过干扰字符），按较短串归一化
    lcs = sum(blk.size for blk in sm.get_matching_blocks())
    lcs_ratio = lcs / max(1, min(len(a), len(b)))

    # 4) 字符级相似度
    ratio = sm.ratio()

    # 5) 字符集 Jaccard
    sa, sb = set(a), set(b)
    jaccard = len(sa & sb) / max(1, len(sa | sb))

    return max(0.0, min(1.0, max(lcs_ratio, ratio, jaccard)))


def _crop_player_name_roi(frame_bgr: np.ndarray,
                          box: dict) -> np.ndarray | None:
    """裁剪玩家昵称区域（昵称浮字在头顶，战斗中常被伤害数字压进玩家框内）。

    ROI = [x1, y1-上方延伸, x2, y2+底部余量]，覆盖"头顶上方 + 整个玩家框"，
    保证昵称无论浮在头顶上方还是被战斗数字遮挡落到框内都能被 OCR 捕获。
    放大 OCR_UPSCALE 倍后返回。
    保留 BGR 彩色让 RapidOCR 内部做检测与识别，避免自适应二值化丢失描边信息。
    """
    h, w = frame_bgr.shape[:2]
    x1 = max(0, int(box["x1"]))
    x2 = min(w, int(box["x2"]))
    y1 = max(0, int(box["y1"]) - PLAYER_NAME_ROI_ABOVE)
    y2 = min(h, int(box["y2"]) + PLAYER_NAME_ROI_BELOW)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    roi = frame_bgr[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    return cv2.resize(roi, None, fx=OCR_UPSCALE, fy=OCR_UPSCALE,
                      interpolation=cv2.INTER_CUBIC)


def get_yolo_device() -> str | int:
    """根据当前环境自动选择 YOLO 推理设备。

    检测顺序: NVIDIA CUDA → AMD/Intel DirectML → CPU
    """
    # 1) NVIDIA CUDA
    if torch.cuda.is_available():
        _logger.info("YOLO 设备: CUDA (NVIDIA GPU)")
        return 0  # ultralytics 用整数 0 表示 GPU0

    # 2) AMD / Intel 显卡 → DirectML
    try:
        import torch_directml
        dml = torch_directml.device()
        _logger.info("YOLO 设备: DirectML (AMD/Intel GPU)")
        return dml
    except ImportError:
        pass

    # 3) CPU
    _logger.info("YOLO 设备: CPU")
    return "cpu"


def move_model_to_device(yolo_model) -> None:
    """将 ultralytics YOLO 模型的底层 PyTorch 模型迁移到当前设备。

    调用时机: YOLO(yolo_path) 之后、首次 predict 之前。
    """
    device = get_yolo_device()
    if device == "cpu":
        return  # 已经是 CPU，无需移动

    try:
        if isinstance(device, int):
            # CUDA: device=0
            yolo_model.model.to(f"cuda:{device}")
        else:
            # DirectML device 对象
            yolo_model.model.to(device)
    except Exception as e:
        _logger.warning(f"模型迁移到 {device} 失败: {e}，保持 CPU 运行")

# ============================================================
# YOLO 检测（一次推理：怪物 + 玩家）
# ============================================================

def detect_objects(model, frame_bgr: np.ndarray) -> tuple[list[dict], list[dict]]:
    """YOLO 一次推理 → (怪物列表, 玩家列表)。

    怪物按「怪物类型标签」MONSTER_CLASS_NAMES 判定；
    玩家按「玩家类别名」PLAYER_CLASS_NAME 判定。
    """
    results = model.predict(frame_bgr, conf=YOLO_CONF, iou=YOLO_IOU,
                            device=get_yolo_device(), verbose=False)
    monsters: list[dict] = []
    players: list[dict] = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            cls_id = int(box.cls)
            cls_name = config.CLASS_NAMES.get(cls_id, "")
            conf = float(box.conf)
            xyxy = box.xyxy.tolist()[0]
            x1, y1, x2, y2 = xyxy
            item = {
                "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "conf": conf, "cls": cls_id, "cls_name": cls_name,
            }
            if cls_name in MONSTER_CLASS_NAMES:
                monsters.append(item)
            elif cls_name == PLAYER_CLASS_NAME:
                players.append(item)
    return monsters, players


def detect_monsters(model, frame_bgr: np.ndarray) -> list[dict]:
    """YOLO推理 → 只返回怪物（按怪物类型标签判定）。"""
    monsters, _ = detect_objects(model, frame_bgr)
    return monsters


def find_character_by_ocr(frame_bgr: np.ndarray,
                          players: list[dict],
                          char_name: str | None = None) -> tuple[int, int, float] | None:
    """从玩家列表中 OCR 识别昵称，模糊匹配角色名，返回角色屏幕坐标。

    Args:
        frame_bgr: 游戏截图 (BGR)
        players: detect_objects 返回的玩家框列表
        char_name: 角色名（为空时用 config.CHARACTER_NAME）

    Returns:
        (cx, cy, score) 或 None。score 为模糊匹配相似度。
    """
    target = (char_name or CHARACTER_NAME or "").strip()
    if not target or not players:
        return None
    best: tuple[int, int, float] | None = None
    best_score = 0.0
    for p in players:
        roi = _crop_player_name_roi(frame_bgr, p)
        text = _ocr_text(roi) if roi is not None else ""
        if not text:
            continue
        score = _name_match(text, target)
        if score > best_score:
            best_score = score
            best = (int(p["cx"]), int((p["y1"] + p["y2"]) / 2), score)
    if best is not None and best_score >= OCR_NAME_MATCH_THRESHOLD:
        return best
    return None


# ============================================================
# 小地图黄点定位
# ============================================================

def find_yellow_dot(mm_bgr: np.ndarray,
                     hsv_lower: np.ndarray | None = None,
                     hsv_upper: np.ndarray | None = None) -> tuple[float, float] | None:
    """在小地图截图中找角色黄点，返回 (x, y) 或 None。
    如传入 hsv_lower/hsv_upper 则使用自定义阈值，否则用 config 默认值。
    """
    lw = hsv_lower if hsv_lower is not None else DOT_HSV_LOWER
    up = hsv_upper if hsv_upper is not None else DOT_HSV_UPPER
    hsv = cv2.cvtColor(mm_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lw, up)
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
    on_rope: bool = False           # 当前是否在绳梯上
    rope_frames: int = 0            # 连续在绳梯上的帧数
    pos_history: list[tuple[float, float]] = field(default_factory=list)  # 最近N帧小地图坐标
    last_perception_time: float = 0.0  # 最近一次成功感知的时间戳

    def record_position(self, x: float, y: float, max_frames: int = 50) -> None:
        """记录当前小地图坐标，保留最近 max_frames 帧。"""
        self.pos_history.append((x, y))
        if len(self.pos_history) > max_frames:
            self.pos_history.pop(0)


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
