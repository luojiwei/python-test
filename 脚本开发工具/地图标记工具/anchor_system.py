"""巡逻路线锚点系统。

AnchorPoint:     表示一个可选锚点（平台端点/绳梯/跳跃/闪现）
AnchorResolver:  从 maps.json 数据构建锚点列表并提供筛选/查询功能
"""

from dataclasses import dataclass, field


# ==================== AnchorPoint ====================

@dataclass
class AnchorPoint:
    """巡逻路线的可选锚点——平台端点 / 绳梯整体 / 跳跃点 / 闪现点。

    对于非平台锚点（绳梯/跳跃/闪现），sub_points 存储其实际端点坐标和
    所属平台信息，供路线绘制时"智能连接最近子端点"使用。
    """
    anchor_id: str         # 如 "plat_0_L", "rope_1", "jump_0"
    anchor_type: str       # "plat_left" | "plat_right" | "rope" | "jump" | "flash"
    label: str             # 如 "P0-左端点", "R1", "J0"
    x: int                 # minimap 坐标 X（锚点中心/平台端点位置）
    y: int                 # minimap 坐标 Y
    parent_id: str         # 如 "platform_0"
    parent_index: int      # 序号
    platform_ids: list[str] = field(default_factory=list)
    # 该锚点涉及的平台列表。平台端点 = [自己平台]，绳梯/跳跃/闪现 = [低平台, 高平台]
    sub_points: list[dict] = field(default_factory=list)
    # [{"platform_id": "platform_0", "x": 60, "y": 88}, ...]
    # 绳梯: 2 个子点（bottom→低平台, top→高平台）
    # 跳跃/闪现: 2 个子点（from→出发平台, to→目标平台）


# ==================== AnchorResolver ====================

class AnchorResolver:
    """从 maps.json 的地图数据构建所有可选锚点列表，含平台邻接信息。

    核心职责：
    1. 枚举所有锚点（平台左/右端点、绳梯、跳跃点、闪现点）
    2. 为每个非平台锚点记录其子端点各自属于哪个平台
    3. 记录各平台与锚点的关联关系，支持下拉框联动筛选
    4. 提供"获取从某锚点出发的可达锚点"方法
    """

    def __init__(self, platforms: list[dict], ropes: list[dict],
                 jumps: list[dict], flashes: list[dict]):
        self._anchors: list[AnchorPoint] = []
        self._by_id: dict[str, AnchorPoint] = {}
        # 平台 → 该平台可达的锚点 ID 列表
        self._platform_anchors: dict[str, list[str]] = {}
        self._build(platforms, ropes, jumps, flashes)

    def _find_platform_for_xy(self, px: float, py: float,
                               platforms: list[dict], exclude_id: str = "") -> str | None:
        """判断 minimap 坐标 (px, py) 最接近哪个平台。"""
        best_id, best = None, float("inf")
        for p in platforms:
            pid = p.get("id", "")
            if pid == exclude_id:
                continue
            lx = p["left_endpoint"]["x"] - 6
            rx = p["right_endpoint"]["x"] + 6
            if not (lx <= px <= rx):
                continue
            ymin, ymax = p["min_y"] - 4, p["max_y"] + 4
            if not (ymin <= py <= ymax):
                continue
            dist = abs(py - p["avg_y"])
            if dist < best:
                best, best_id = dist, pid
        return best_id

    def _build(self, platforms, ropes, jumps, flashes) -> None:
        # --- 平台端点 ---
        for i, p in enumerate(platforms):
            pid: str = p.get("id", f"platform_{i}")
            self._platform_anchors.setdefault(pid, [])
            le, re = p["left_endpoint"], p["right_endpoint"]

            a = AnchorPoint(f"plat_{i}_L", "plat_left",
                            f"P{i}-左端点", int(le["x"]), int(le["y"]),
                            pid, i, [pid], [])
            self._anchors.append(a); self._by_id[a.anchor_id] = a
            self._platform_anchors[pid].append(a.anchor_id)

            a = AnchorPoint(f"plat_{i}_R", "plat_right",
                            f"P{i}-右端点", int(re["x"]), int(re["y"]),
                            pid, i, [pid], [])
            self._anchors.append(a); self._by_id[a.anchor_id] = a
            self._platform_anchors[pid].append(a.anchor_id)

        # --- 绳梯：匹配 bottom/top 各自属于哪个平台 ---
        for i, r in enumerate(ropes):
            tx, ty = r["top"]["x"], r["top"]["y"]
            bx, by = r["bottom"]["x"], r["bottom"]["y"]
            mx, my = int((tx + bx) / 2), int((ty + by) / 2)

            plat_bot = self._find_platform_for_xy(bx, by, platforms)
            plat_top = self._find_platform_for_xy(tx, ty, platforms)

            sub = []
            pids = []
            if plat_bot:
                sub.append({"platform_id": plat_bot, "x": int(bx), "y": int(by)})
                pids.append(plat_bot)
                self._platform_anchors.setdefault(plat_bot, [])
                self._platform_anchors[plat_bot].append(f"rope_{i}")
            if plat_top and plat_top != plat_bot:
                sub.append({"platform_id": plat_top, "x": int(tx), "y": int(ty)})
                pids.append(plat_top)
                self._platform_anchors.setdefault(plat_top, [])
                self._platform_anchors[plat_top].append(f"rope_{i}")

            a = AnchorPoint(f"rope_{i}", "rope", f"R{i}", mx, my,
                            f"rope_{i}", i, pids, sub)
            self._anchors.append(a); self._by_id[a.anchor_id] = a

        # --- 跳跃点：匹配 from/to 各自属于哪个平台 ---
        for i, j in enumerate(jumps):
            fx, fy = j["from"]["x"], j["from"]["y"]
            tx, ty = j["to"]["x"], j["to"]["y"]
            mx, my = int((fx + tx) / 2), int((fy + ty) / 2)

            plat_from = self._find_platform_for_xy(fx, fy, platforms)
            plat_to = self._find_platform_for_xy(tx, ty, platforms, exclude_id=plat_from or "")

            sub = []; pids = []
            if plat_from:
                sub.append({"platform_id": plat_from, "x": int(fx), "y": int(fy)})
                pids.append(plat_from)
                self._platform_anchors.setdefault(plat_from, [])
                self._platform_anchors[plat_from].append(f"jump_{i}")
            if plat_to and plat_to != plat_from:
                sub.append({"platform_id": plat_to, "x": int(tx), "y": int(ty)})
                pids.append(plat_to)
                self._platform_anchors.setdefault(plat_to, [])
                self._platform_anchors[plat_to].append(f"jump_{i}")

            a = AnchorPoint(f"jump_{i}", "jump", f"J{i}", mx, my,
                            f"jump_{i}", i, pids, sub)
            self._anchors.append(a); self._by_id[a.anchor_id] = a

        # --- 闪现点：匹配 from/to 各自属于哪个平台 ---
        for i, fl in enumerate(flashes):
            fx, fy = fl["from"]["x"], fl["from"]["y"]
            tx, ty = fl["to"]["x"], fl["to"]["y"]
            mx, my = int((fx + tx) / 2), int((fy + ty) / 2)

            plat_from = self._find_platform_for_xy(fx, fy, platforms)
            plat_to = self._find_platform_for_xy(tx, ty, platforms, exclude_id=plat_from or "")

            sub = []; pids = []
            if plat_from:
                sub.append({"platform_id": plat_from, "x": int(fx), "y": int(fy)})
                pids.append(plat_from)
                self._platform_anchors.setdefault(plat_from, [])
                self._platform_anchors[plat_from].append(f"flash_{i}")
            if plat_to and plat_to != plat_from:
                sub.append({"platform_id": plat_to, "x": int(tx), "y": int(ty)})
                pids.append(plat_to)
                self._platform_anchors.setdefault(plat_to, [])
                self._platform_anchors[plat_to].append(f"flash_{i}")

            a = AnchorPoint(f"flash_{i}", "flash", f"F{i}", mx, my,
                            f"flash_{i}", i, pids, sub)
            self._anchors.append(a); self._by_id[a.anchor_id] = a

    def get_by_id(self, anchor_id: str) -> AnchorPoint | None:
        return self._by_id.get(anchor_id)

    def get_sub_point_for_platform(self, anchor_id: str, target_platform: str) -> dict | None:
        """获取锚点的子端点中属于 target_platform 的那个，用于路线绘制。"""
        a = self._by_id.get(anchor_id)
        if not a or not a.sub_points:
            return None
        for sp in a.sub_points:
            if sp["platform_id"] == target_platform:
                return sp
        # 没找到精确匹配 → 返回第一个（最近原则）
        if a.sub_points:
            return a.sub_points[0]
        return None

    def get_filtered_anchors(self, selected_id: str) -> list[AnchorPoint]:
        """根据左边选中的锚点，返回右边可选的锚点列表（含同平台另一端点和所有连接点）。"""
        sa = self._by_id.get(selected_id)
        if not sa:
            return self._anchors.copy()

        # 收集选中锚点涉及的所有平台
        reachable_platforms: set[str] = set(sa.platform_ids)

        # 从这些平台出发，收集所有可达锚点 ID
        allowed_ids: set[str] = set()
        for pid in reachable_platforms:
            for aid in self._platform_anchors.get(pid, []):
                allowed_ids.add(aid)

        # 过滤：排除自身，排除完全无关的锚点
        result: list[AnchorPoint] = []
        for a in self._anchors:
            if a.anchor_id == selected_id:
                continue
            if a.anchor_id in allowed_ids:
                result.append(a)
        return result

    @property
    def grouped_options(self) -> list[tuple[str, str]]:
        """返回 [(anchor_id, display_label), ...] 按分组排序（完整列表）。"""
        return self._group_anchors(self._anchors)

    def grouped_filtered_options(self, selected_id: str) -> list[tuple[str, str]]:
        """返回筛选后的 [(anchor_id, display_label), ...]（联动过滤版）。"""
        filtered = self.get_filtered_anchors(selected_id)
        return self._group_anchors(filtered)

    @staticmethod
    def _group_anchors(anchors: list[AnchorPoint]) -> list[tuple[str, str]]:
        groups: dict[str, list[tuple[str, str, int]]] = {
            "平台端点": [], "绳梯": [], "跳跃点": [], "闪现点": [],
        }
        for a in anchors:
            if a.anchor_type in ("plat_left", "plat_right"):
                groups["平台端点"].append((a.anchor_id, f"{a.label}  ({a.x},{a.y})", a.parent_index))
            elif a.anchor_type == "rope":
                groups["绳梯"].append((a.anchor_id, f"{a.label}  ({a.x},{a.y})", a.parent_index))
            elif a.anchor_type == "jump":
                groups["跳跃点"].append((a.anchor_id, f"{a.label}  ({a.x},{a.y})", a.parent_index))
            elif a.anchor_type == "flash":
                groups["闪现点"].append((a.anchor_id, f"{a.label}  ({a.x},{a.y})", a.parent_index))
        for k in groups:
            groups[k].sort(key=lambda x: x[2])
        result: list[tuple[str, str]] = []
        for gn in ["平台端点", "绳梯", "跳跃点", "闪现点"]:
            if groups[gn]:
                if result:
                    result.append(("__SEP__", f"── {gn} ──"))
                else:
                    result.append(("__SEP__", f"── {gn} ──"))
                for aid, label, _ in groups[gn]:
                    result.append((aid, label))
        return result
