"""world_model.py — 世界模型：平台、边、邻接表、路径查找"""

import json
from collections import deque
from dataclasses import dataclass, field


@dataclass
class WorldModel:
    platforms: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    adjacency: dict[str, list[dict]] = field(default_factory=dict)
    mm_region: list[int] = field(default_factory=lambda: [8, 97, 128, 208])

    def find_platform(self, px: float, py: float) -> str | None:
        best_id, best = None, float("inf")
        for p in self.platforms:
            lx = p["left_endpoint"]["x"] - 4
            rx = p["right_endpoint"]["x"] + 4
            if not (lx <= px <= rx):
                continue
            pmin = p["min_y"] - 4
            pmax = p["max_y"] + 4
            if not (pmin <= py <= pmax):
                continue
            dist = abs(py - p["avg_y"])
            if dist < best:
                best, best_id = dist, p["id"]
        return best_id

    def is_top(self, pid: str) -> bool:
        return not any(e for e in self.adjacency.get(pid, [])
                       if e.get("direction") == "up" or e["type"] in ("jump", "flash"))

    def is_bottom(self, pid: str) -> bool:
        return not any(e for e in self.adjacency.get(pid, [])
                       if e.get("direction") == "down" or e["type"] in ("jump", "flash"))

    @staticmethod
    def _platform_order(pid: str) -> int:
        try:
            return int(pid.split("_")[-1])
        except (ValueError, AttributeError):
            return 9999

    def find_nearest_exit(self, pid: str, direction: str,
                          player_x: float = 0.0) -> dict | None:
        if pid not in self.adjacency:
            return None

        # 收集所有从当前平台出发的直接出口
        direct: list[dict] = []
        for e in self.adjacency.get(pid, []):
            if direction == "up":
                goes = (e["type"] == "rope" and e.get("direction") == "up") or \
                       e["type"] in ("jump", "flash")
            else:
                goes = (e["type"] == "rope" and e.get("direction") == "down") or \
                       e["type"] in ("jump", "flash")
            if goes:
                direct.append(e)

        if direct:
            reverse = (direction == "down")
            direct.sort(key=lambda e: self._platform_order(e["to_platform"]), reverse=reverse)
            return direct[0]

        # 没有直接出口，BFS 深搜经过中间平台
        visited: set[str] = {pid}
        q: deque[tuple[str, list[dict]]] = deque([(pid, [])])
        while q:
            node, path = q.popleft()
            for e in self.adjacency.get(node, []):
                nxt = e["to_platform"]
                if nxt in visited:
                    continue
                visited.add(nxt)
                new_path = path + [e]
                if direction == "up":
                    goes = (e["type"] == "rope" and e.get("direction") == "up") or \
                           e["type"] in ("jump", "flash")
                    if goes:
                        return new_path[0] if new_path else e
                else:
                    goes = (e["type"] == "rope" and e.get("direction") == "down") or \
                           e["type"] in ("jump", "flash")
                    if goes:
                        return new_path[0] if new_path else e
                q.append((nxt, new_path))
        return None

    def get_exit_minimap_x(self, edge: dict) -> float:
        if edge["type"] == "rope":
            return edge.get("to_pt", {}).get("x", 0)
        # jump / flash: 用 to 字段
        return edge.get("to", {}).get("x", 0)

    def get_exit_target_y(self, edge: dict) -> float | None:
        if edge["type"] != "rope":
            return None
        return edge.get("to_pt", {}).get("y")


def load_world_model(path: str) -> WorldModel:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    adjacency: dict[str, list[dict]] = data.get("adjacency", {})
    edges: list[dict] = data.get("edges", [])

    # 容错：如果 JSON 中缺少 adjacency，从 edges 自动构建
    if not adjacency and edges:
        adjacency = {}
        for e in edges:
            from_pid: str = e.get("from_platform", "")
            if from_pid:
                adjacency.setdefault(from_pid, []).append(e)

    return WorldModel(
        platforms=data.get("platforms", []),
        edges=edges,
        adjacency=adjacency,
        mm_region=data.get("mm_region", [8, 97, 128, 208]),
    )
