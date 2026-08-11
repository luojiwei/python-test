"""auto_potion.py — HP/MP 数字识别 + 自动吃药决策。

纯 cv2 实现，不依赖外部 OCR 引擎。
"""

import re
import time

import cv2
import numpy as np

# ============================================================
# 数字识别（固定字体 "99/100" 格式）
# ============================================================

# 参考数字模板（7×12 像素二值化，用于 matchTemplate）
_DIGIT_TEMPLATES: dict[str, np.ndarray] = {}
_TEMPLATE_INITIALIZED = False

# 几何分类用的宽高范围（基于观察：HP/MP 字符约 5x7-8 像素）
_DIGIT_MIN_W = 3
_DIGIT_MIN_H = 6
_BRACKET_MAX_W = 2
_BRACKET_MIN_H = 8


def _classify_by_shape(char_img: np.ndarray) -> str | None:
    """基于几何特征预分类：
    - 高瘦（w≤2, h≥8）：[ 或 ]
    - 中宽（w≥3）：数字或 /
    返回 '[' / ']' / '?' / None
    """
    h, w = char_img.shape
    if w < 2 or h < 5:
        return None
    if w <= _BRACKET_MAX_W and h >= _BRACKET_MIN_H:
        # 比较左右两列在中间行的填充量
        middle_top = max(1, h // 4)
        middle_bot = h - middle_top
        # 左侧列
        left = char_img[middle_top:middle_bot, :1].sum()
        # 右侧列
        right = char_img[middle_top:middle_bot, w-1:w].sum()
        # [ 的左列填满，右列空；] 反之
        return '[' if left > right else ']'
    return '?'


def _init_templates():
    """用 PIL 渲染标准数字 0-9 + '/[]' 生成参考模板。"""
    global _TEMPLATE_INITIALIZED
    if _TEMPLATE_INITIALIZED:
        return
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for name in ["Consolas", "Courier New", "Lucida Console", "monospace"]:
        try:
            font = ImageFont.truetype(f"C:/Windows/Fonts/{name}.ttf", 18)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    # 模板只放数字和斜杠（括号用几何分类）
    chars = "0123456789/"
    for ch in chars:
        img = Image.new("L", (14, 20), 0)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), ch, font=font)
        x = (14 - (bbox[2] - bbox[0])) // 2
        y = (20 - (bbox[3] - bbox[1])) // 2
        draw.text((x, y - bbox[1]), ch, fill=255, font=font)
        arr = np.array(img)
        _, arr = cv2.threshold(arr, 128, 255, cv2.THRESH_BINARY)
        coords = cv2.findNonZero(arr)
        if coords is not None:
            x, y, w, h = cv2.boundingRect(coords)
            arr = arr[y:y + h, x:x + w]
        _DIGIT_TEMPLATES[ch] = arr
    _TEMPLATE_INITIALIZED = True


# 5x7 像素数字字模（从游戏截图实采）
_PIXEL_FONT_5x7 = {
    '0': [
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ],
    '1': [
        "..#..",
        ".##..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        ".###.",
    ],
    '2': [
        ".###.",
        "#...#",
        "....#",
        "...#.",
        "..#..",
        ".#...",
        "#####",
    ],
    '3': [
        ".###.",
        "#...#",
        "....#",
        "..##.",
        "....#",
        "#...#",
        ".###.",
    ],
    '4': [
        "#...#",
        "#...#",
        "#...#",
        "#####",
        "....#",
        "....#",
        "....#",
    ],
    '5': [
        "#####",
        "#....",
        "#....",
        ".###.",
        "....#",
        "#...#",
        ".###.",
    ],
    '6': [
        ".###.",
        "#...#",
        "#....",
        "####.",
        "#...#",
        "#...#",
        ".###.",
    ],
    '7': [
        "#####",
        "....#",
        "...#.",
        "..#..",
        ".#...",
        "#....",
        "#....",
    ],
    '8': [
        ".###.",
        "#...#",
        "#...#",
        ".###.",
        "#...#",
        "#...#",
        ".###.",
    ],
    '9': [
        ".###.",
        "#...#",
        "#...#",
        ".####",
        "....#",
        "#...#",
        ".###.",
    ],
    '/': [
        "......#",
        ".....#.",
        "....#..",
        "...#...",
        "..#....",
        ".#.....",
        "#......",
    ],
}


def _classify_digit(char_img: np.ndarray) -> str | None:
    """通过精确像素模式匹配识别 0-9 和 /"""
    h, w = char_img.shape
    if w < 2 or h < 5:
        return None

    _, binary = cv2.threshold(char_img, 128, 255, cv2.THRESH_BINARY)
    white = (binary > 0)

    # 归一化到 7 高
    if h != 7:
        scale_y = 7 / h
        white_resized = cv2.resize(white.astype(np.uint8) * 255,
                                     None, fx=scale_y, fy=scale_y,
                                     interpolation=cv2.INTER_AREA) > 128
    else:
        white_resized = white

    # 与字模逐个对比（允许不同宽度匹配）
    best_char = None
    best_score = -1.0
    for ch, pattern in _PIXEL_FONT_5x7.items():
        template = np.array([[p == '#' for p in row] for row in pattern], dtype=bool)
        # 尺寸不匹配：尝试裁剪或填充到模板尺寸
        th, tw = template.shape
        rh, rw = white_resized.shape
        if rh != th:
            continue
        if rw == tw:
            cmp = white_resized
        elif rw < tw:
            # 输入窄：居中补白
            pad = tw - rw
            left_pad = pad // 2
            right_pad = pad - left_pad
            cmp = np.pad(white_resized, ((0, 0), (left_pad, right_pad)),
                         constant_values=False)
        else:
            # 输入宽：从两侧各裁一半（`/` 7 列对应模板 5 列时裁掉两侧）
            excess = rw - tw
            left_trim = excess // 2
            cmp = white_resized[:, left_trim:left_trim + tw]
        score = (template == cmp).sum() / template.size
        if score > best_score:
            best_score = score
            best_char = ch

    if best_score < 0.7:
        return None
    return best_char


def read_hp_mp(region: np.ndarray) -> dict:
    """从 HP/MP 区域图像中识别当前值和最大值。

    支持格式: "99/100", "[580/630]" 等

    Args:
        region: 裁剪好的 HP 或 MP 区域图像 (BGR)

    Returns:
        {"current": int, "max": int} 或 None（识别失败）
    """
    if region is None or region.size == 0:
        return None

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    # OTSU 全局阈值
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # 找轮廓 → 单个字符
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    chars = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < 2 or h < 4:
            continue
        if w > binary.shape[1] * 0.5 or h > binary.shape[0] * 0.95:
            continue
        char_img = binary[y:y + h, x:x + w]
        # 先用几何特征分类括号
        shape = _classify_by_shape(char_img)
        if shape in ('[', ']'):
            chars.append((x, shape))
            continue
        # 否则走几何数字分类
        ch = _classify_digit(char_img)
        if ch is None:
            ch = '?'
        chars.append((x, ch))

    if not chars:
        return None

    chars.sort(key=lambda c: c[0])
    text = "".join(ch[1] for ch in chars)
    # 括号已被识别为 [ 和 ]
    m = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if not m:
        digits = re.findall(r'\d+', text)
        if len(digits) >= 2:
            return {"current": int(digits[0]), "max": int(digits[1])}
        elif len(digits) == 1:
            return {"current": int(digits[0]), "max": int(digits[0])}
        return None

    return {"current": int(m.group(1)), "max": int(m.group(2))}


# ============================================================
# 自动药水决策
# ============================================================

class HPMPBuffer:
    """血量/蓝量数值记录容器，过滤 OCR 偶发错误。

    - 保留最近 20 帧有效读数
    - 若新读数较上一帧骤降超 50%，丢弃（疑似 OCR 误读）
    - 取中位数作为最终判断值
    """

    def __init__(self, max_size: int = 5) -> None:
        self._data: list[int] = []
        self._max_size = max_size
        self._last_potion_time: float = 0.0  # 上次吃药时间，用于放宽突变检查

    def feed(self, current: int, now: float) -> None:
        """尝试记录一次 HP/MP 读数。"""
        if not self._data:
            self._data.append(current)
            return

        prev = self._data[-1]
        # 刚吃过药 → 放宽阈值，允许大幅回升
        if now - self._last_potion_time < 2.0:
            self._data.append(current)
        elif current < prev * 0.5:
            # 骤降超 50%，疑似 OCR 错误，丢弃
            return
        else:
            self._data.append(current)

        # 滚动
        while len(self._data) > self._max_size:
            self._data.pop(0)

    def mark_potion_used(self, now: float) -> None:
        """标记刚吃了药，允许下一次读数大幅回升。"""
        self._last_potion_time = now

    def get_value(self) -> int | None:
        """取中位数，需要至少 3 帧才生效。"""
        if len(self._data) < 3:
            return None
        sorted_data = sorted(self._data)
        mid = len(sorted_data) // 2
        return sorted_data[mid]


class AutoPotion:
    """自动血/蓝管理。

    Args:
        hp_rect: HP 数字区域 [x1, y1, x2, y2]
        mp_rect: MP 数字区域 [x1, y1, x2, y2]
        key_sender: KeySender 实例
    """

    def __init__(self, hp_rect=None, mp_rect=None, key_sender=None):
        self.hp_rect = hp_rect or []
        self.mp_rect = mp_rect or []
        self.key_sender = key_sender

        # 配置（由 GUI 控制）
        self.hp_enabled = False
        self.hp_mode = "百分比"     # "百分比" | "固定值"
        self.hp_threshold = 50      # 默认 50%
        self.hp_key = "Q"

        self.mp_enabled = False
        self.mp_mode = "百分比"
        self.mp_threshold = 30
        self.mp_key = "W"

        # 防连发
        self._last_hp_use = 0.0
        self._last_mp_use = 0.0
        self.potion_cooldown = 2.0  # 2 秒冷却
        self._last_diag = 0.0       # 诊断日志节流

        # 读数缓冲（过滤 OCR 偶发误读）
        self._hp_buf = HPMPBuffer()
        self._mp_buf = HPMPBuffer()

    def update(self, frame: np.ndarray, actions=None, log_cb=None) -> bool:
        """每帧调用：读取 HP/MP 并决定是否吃药。返回 True 表示已使用药水。"""
        if not self.key_sender:
            return False
        fh, fw = frame.shape[:2]
        now = time.time()
        used = False

        def _safe_rect(rect):
            if len(rect) != 4:
                return None
            x1, y1, x2, y2 = rect
            if x2 > fw or y2 > fh or x2 <= x1 or y2 <= y1:
                return None
            return frame[y1:y2, x1:x2]

        # HP
        if self.hp_enabled and now - self._last_hp_use > self.potion_cooldown:
            region = _safe_rect(self.hp_rect)
            if region is not None:
                result = read_hp_mp(region)
                if result and result["current"] > 0 and result["max"] > 0:
                    max_hp = result["max"]
                    cur = result["current"]
                    # 简单平滑：最近 3 帧中位数
                    self._hp_buf.feed(cur, now)
                    buf_val = self._hp_buf.get_value()
                    # buffer 不够时直接用当前值
                    hp_val = buf_val if buf_val is not None else cur
                    should_use = False
                    if self.hp_mode == "百分比":
                        should_use = hp_val / max_hp * 100 < self.hp_threshold
                    else:
                        should_use = hp_val < self.hp_threshold
                    if should_use:
                        self.key_sender.force_release_all()
                        self.key_sender.tap(self.hp_key)
                        self._last_hp_use = now
                        self._hp_buf.mark_potion_used(now)
                        used = True
                        if log_cb:
                            log_cb(
                                f"吃药(HP): {buf_val}/{max_hp} "
                                f"< {self.hp_threshold}{'%' if self.hp_mode == '百分比' else ''}"
                            )

        # MP
        if self.mp_enabled and now - self._last_mp_use > self.potion_cooldown:
            region = _safe_rect(self.mp_rect)
            if region is not None:
                result = read_hp_mp(region)
                if result and result["current"] > 0 and result["max"] > 0:
                    max_mp = result["max"]
                    cur = result["current"]
                    self._mp_buf.feed(cur, now)
                    buf_val = self._mp_buf.get_value()
                    mp_val = buf_val if buf_val is not None else cur
                    should_use = False
                    if self.mp_mode == "百分比":
                        should_use = mp_val / max_mp * 100 < self.mp_threshold
                    else:
                        should_use = mp_val < self.mp_threshold
                    if should_use:
                        self.key_sender.force_release_all()
                        self.key_sender.tap(self.mp_key)
                        self._last_mp_use = now
                        self._mp_buf.mark_potion_used(now)
                        used = True
                        if log_cb:
                            log_cb(
                                f"吃药(MP): {buf_val}/{max_mp} "
                                f"< {self.mp_threshold}{'%' if self.mp_mode == '百分比' else ''}"
                            )
        # 低频诊断日志（每 5 秒一次）
        if now - self._last_diag > 5.0 and log_cb:
            self._last_diag = now
            if self.hp_enabled:
                hp_region = _safe_rect(self.hp_rect)
                if hp_region is not None:
                    hr = read_hp_mp(hp_region)
                    if hr:
                        buf_val = self._hp_buf.get_value()
                        buf_s = f" buf={buf_val}" if buf_val is not None else ""
                        if self.hp_mode == "百分比":
                            trigger = (buf_val or hr["current"]) / max(1, hr["max"]) * 100 < self.hp_threshold
                        else:
                            trigger = (buf_val or hr["current"]) < self.hp_threshold
                        ratio = hr["current"] / max(1, hr["max"]) * 100
                        log_cb(f"[药水诊断] HP={hr['current']}/{hr['max']} ({ratio:.0f}%){buf_s}"
                               f" 阈值{self.hp_mode}{self.hp_threshold}"
                               f" {'→吃药' if trigger else '→跳过'}")
                    else:
                        log_cb(f"[药水诊断] HP识别失败")
                else:
                    log_cb(f"[药水诊断] HP区域越界 {self.hp_rect}")
            if self.mp_enabled:
                mp_region = _safe_rect(self.mp_rect)
                if mp_region is not None:
                    mr = read_hp_mp(mp_region)
                    if mr:
                        buf_val = self._mp_buf.get_value()
                        buf_s = f" buf={buf_val}" if buf_val is not None else ""
                        if self.mp_mode == "百分比":
                            trigger = (buf_val or mr["current"]) / max(1, mr["max"]) * 100 < self.mp_threshold
                        else:
                            trigger = (buf_val or mr["current"]) < self.mp_threshold
                        ratio = mr["current"] / max(1, mr["max"]) * 100
                        log_cb(f"[药水诊断] MP={mr['current']}/{mr['max']} ({ratio:.0f}%){buf_s}"
                               f" 阈值{self.mp_mode}{self.mp_threshold}"
                               f" {'→吃蓝' if trigger else '→跳过'}")
                    else:
                        log_cb(f"[药水诊断] MP识别失败")
                else:
                    log_cb(f"[药水诊断] MP区域越界 {self.mp_rect}")

        return used
