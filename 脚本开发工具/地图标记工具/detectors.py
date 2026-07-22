"""地图元素检测器集合。

包含：
- PlatformRecorder: 平台位置记录器（去重存储）
- RopeDetector:     绳梯顶/底检测器（滑动窗口）
- JumpDetector:     平台跳跃检测器（滑动窗口）
- FlashDetector:    闪现/瞬移检测器（单帧大位移）
"""

import time


# ==================== PlatformRecorder ====================

class PlatformRecorder:
    """Simple position recorder for platform marking — collects (x,y), deduplicates near-dupes."""

    def __init__(self, y_offset: int = 2):
        self._positions: list = []  # [(x, y), ...]
        self.y_offset: int = y_offset

    def add(self, x, y):
        ix, iy = int(x), int(y)
        # Skip if last point is within 1px in both axes
        if self._positions:
            px, py = self._positions[-1]
            if abs(ix - px) <= 1 and abs(iy - py) <= 1:
                return
        self._positions.append((ix, iy))

    def get_positions(self):
        return self._positions.copy()

    @property
    def count(self):
        return len(self._positions)

    def reset(self):
        self._positions = []


# ==================== RopeDetector ====================

class RopeDetector:
    """Detects rope ladder top/bottom from character position sequence.

    Detection rules (using a sliding window of the last 10 recorded positions):
    - TOP:    first 9 y-values are stable (range <= threshold), the 10th y INCREASES
              (character starts climbing down → y goes UP in minimap coordinate)
              → record the 9th position as the rope ladder top.
    - BOTTOM: all 10 y-values are non-decreasing (tolerance applied), and the jump
              from position 9 to 10 is suddenly large (>= big_drop_ratio × avg jump)
              → record the 9th position as the rope ladder bottom.

    Once a top is detected the detector enters "pending" state and waits for a
    matching bottom.  Each completed pair is stored in `self.ropes`.
    """

    def __init__(self, buffer_size: int = 10, stable_threshold: int = 2,
                 big_drop_ratio: float = 2.0, y_offset: int = 3):
        self.buffer: list = []             # sliding window of (x,y)
        self.buffer_size: int = buffer_size
        self.stable_threshold: int = stable_threshold
        self.big_drop_ratio: float = big_drop_ratio
        self.y_offset: int = y_offset      # px, shift y down to match real rope position
        self.ropes: list = []              # completed: [(tx,ty,bx,by), ...]
        self._pending_top = None           # (x,y) waiting for a matching bottom

    def add(self, x: int, y: int) -> str | None:
        """Feed a new position.  Returns 'top', 'bottom', or None."""
        self.buffer.append((int(x), int(y)))
        if len(self.buffer) > self.buffer_size:
            self.buffer.pop(0)
        if len(self.buffer) < self.buffer_size:
            return None

        y_vals = [p[1] for p in self.buffer]

        if self._pending_top is None:
            # --- looking for rope top ---
            first_9 = y_vals[:9]
            y_range = max(first_9) - min(first_9)
            if y_range <= self.stable_threshold and y_vals[9] > max(first_9):
                self._pending_top = self.buffer[8]  # 9th position (0-indexed)
                self.buffer = []
                return "top"
        else:
            # --- looking for rope bottom ---
            non_dec = all(
                y_vals[i] <= y_vals[i + 1] + self.stable_threshold
                for i in range(9)
            )
            if non_dec:
                increases = [max(0, y_vals[i + 1] - y_vals[i]) for i in range(9)]
                pos_incs = [inc for inc in increases[:8] if inc > 0]
                avg_inc = sum(pos_incs) / len(pos_incs) if pos_incs else 1.0
                last_inc = increases[8]
                if last_inc >= avg_inc * self.big_drop_ratio and last_inc >= 2:
                    bottom = self.buffer[8]
                    self.ropes.append((
                        self._pending_top[0], self._pending_top[1],
                        bottom[0], bottom[1],
                    ))
                    self._pending_top = None
                    self.buffer = []
                    return "bottom"

        return None

    def cancel_pending(self) -> None:
        """Discard a half-detected rope (pending top without bottom)."""
        self._pending_top = None
        self.buffer = []

    def reset(self) -> None:
        """Full reset: clears buffer, pending top and all completed ropes."""
        self.buffer = []
        self.ropes = []
        self._pending_top = None

    @property
    def count(self) -> int:
        return len(self.ropes)

    @property
    def has_pending(self) -> bool:
        """Whether a top has been detected and we are waiting for its bottom."""
        return self._pending_top is not None


# ==================== JumpDetector ====================

class JumpDetector:
    """Detects platform-to-platform jumps from character position sequence.

    Detection (sliding window of last 10 positions):
    Look for a split point where:
      - 3+ positions before the split are stable (y range <= stable_threshold)
      - 3+ positions after  the split are stable
      - The average y before vs after differs by > jump_threshold

    This catches the moment a character leaves one platform and lands on
    another.  The takeoff is the last stable point before the jump,
    the landing is the first stable point after.
    """

    def __init__(self, buffer_size: int = 10, stable_threshold: int = 2,
                 jump_threshold: int = 3, cooldown_frames: int = 15,
                 y_offset: int = 3):
        self.buffer: list = []                 # sliding window
        self.buffer_size: int = buffer_size
        self.stable_threshold: int = stable_threshold
        self.jump_threshold: int = jump_threshold
        self.cooldown_frames: int = cooldown_frames
        self.y_offset: int = y_offset          # px, shift y down to match real position
        self.jumps: list = []   # [(fx,fy,tx,ty), ...]
        self._cooldown: int = 0

    def add(self, x: int, y: int) -> str | None:
        """Feed a new position.  Returns 'jump' or None."""
        if self._cooldown > 0:
            self._cooldown -= 1
            self.buffer.append((int(x), int(y)))
            if len(self.buffer) > self.buffer_size:
                self.buffer.pop(0)
            return None

        self.buffer.append((int(x), int(y)))
        if len(self.buffer) > self.buffer_size:
            self.buffer.pop(0)
        if len(self.buffer) < self.buffer_size:
            return None

        y_vals = [p[1] for p in self.buffer]

        # Try each split point -- at least 3 stable before AND 3 stable after
        for i in range(3, self.buffer_size - 2):
            before = y_vals[:i]
            after = y_vals[i:]

            if max(before) - min(before) > self.stable_threshold:
                continue
            if max(after) - min(after) > self.stable_threshold:
                continue

            avg_before = sum(before) / len(before)
            avg_after = sum(after) / len(after)
            if abs(avg_after - avg_before) > self.jump_threshold:
                # Take the middle of each stable region, not the edge.
                takeoff_mid = i // 2                   # middle of "before"
                landing_mid = i + max(1, (len(after) - 1) // 2)  # middle of "after"
                takeoff = self.buffer[max(0, takeoff_mid)]
                landing = self.buffer[min(len(self.buffer) - 1, landing_mid)]
                self.jumps.append((
                    takeoff[0], takeoff[1], landing[0], landing[1],
                ))
                self.buffer = []
                self._cooldown = self.cooldown_frames
                return "jump"

        return None

    def reset(self) -> None:
        self.buffer = []
        self.jumps = []
        self._cooldown = 0

    @property
    def count(self) -> int:
        return len(self.jumps)


# ==================== FlashDetector ====================

class FlashDetector:
    """Detects flash / teleport from single-frame large displacement (>10 px).

    Unlike jumps (which look for stable→jump→stable patterns), a flash is
    an instant position change in one frame.  Record the pre-flash and
    post-flash positions as a pair.
    """

    def __init__(self, flash_threshold: int = 10, cooldown_frames: int = 20,
                 y_offset: int = 3):
        self.flash_threshold: int = flash_threshold
        self.cooldown_frames: int = cooldown_frames
        self.y_offset: int = y_offset
        self.flashes: list = []      # [(fx,fy,tx,ty,direction), ...]
        self._cooldown: int = 0
        self._prev = None            # (x, y) or None

    def add(self, x: int, y: int) -> str | None:
        """Feed a new position.  Returns 'flash' or None."""
        curr = (int(x), int(y))
        if self._cooldown > 0:
            self._cooldown -= 1
            self._prev = curr
            return None

        if self._prev is not None:
            dx = curr[0] - self._prev[0]
            dy = curr[1] - self._prev[1]
            if (dx * dx + dy * dy) > (self.flash_threshold ** 2):
                direction = "up" if curr[1] < self._prev[1] else "down"
                self.flashes.append(
                    (self._prev[0], self._prev[1], curr[0], curr[1], direction))
                self._cooldown = self.cooldown_frames
                self._prev = curr
                return "flash"

        self._prev = curr
        return None

    def reset(self) -> None:
        self.flashes = []
        self._cooldown = 0
        self._prev = None

    @property
    def count(self) -> int:
        return len(self.flashes)
