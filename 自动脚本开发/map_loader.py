"""map_loader.py — 地图资源统一加载器。

将 start() 中 ~120 行的分散加载逻辑集中为一个 MapLoader 类。
负责：配置解析 → 世界模型 → 巡逻路线 → YOLO → 角色模板。
返回 LoadResult dataclass，main.py 只需一行 loader.load(name)。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from config import PROJECT_DIR, SEARCH_BOTTOM_SKIP_PCT, validate_map_resources
from input_utils import capture_frame, force_foreground
from world_model import WorldModel, load_world_model


@dataclass
class LoadResult:
    """MapLoader 的一次性加载结果。"""
    world_model: WorldModel
    yolo_model: object          # ultralytics.YOLO
    template: np.ndarray        # 角色名模板图像
    search_region: tuple[int, int, int, int]  # (x, y, w, h)
    map_cfg: dict               # config.json 原始数据
    patrol_route_names: list[str] = field(default_factory=list)
    patrol_all_routes: list[list[tuple[float, float]]] = field(default_factory=list)
    patrol_waypoints: list[tuple[float, float]] = field(default_factory=list)
    mm_region: tuple[int, int, int, int] = (0, 0, 0, 0)


class MapLoader:
    """地图资源统一加载器。"""

    def __init__(self, status_cb=None, log_cb=None):
        self._status = status_cb or (lambda s: None)
        self._log = log_cb or (lambda s: None)

    def load(self, map_name: str, target_hwnd: int,
             default_route_idx: int = 0) -> LoadResult:
        """加载指定地图的所有资源。

        Args:
            map_name: 地图名称
            target_hwnd: 游戏窗口句柄
            default_route_idx: 默认选中的巡逻路线索引

        Returns:
            LoadResult 包含所有加载的资源

        Raises:
            FileNotFoundError: 资源文件缺失
            RuntimeError: 加载失败
        """
        missing = validate_map_resources(map_name)
        if missing:
            msg = "缺少以下文件:\n" + "\n".join(f"  • {m}" for m in missing)
            raise FileNotFoundError(msg)

        map_dir = PROJECT_DIR / "maps" / map_name

        # 1. 配置
        config_path = map_dir / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            map_cfg = json.load(f)

        template_rect = tuple(map_cfg.get("template_rect", [85, 728, 150, 745]))
        mm_region = tuple(map_cfg.get("mm_region", [8, 97, 128, 208]))

        # 2. 世界模型
        self._status(f"加载世界模型 [{map_name}]...")
        wm_path = str(map_dir / "world_model.json")
        wm = load_world_model(wm_path)
        wm.mm_region = list(mm_region)
        self._log(f"世界模型: {len(wm.platforms)} 平台, {len(wm.edges)} 边")

        # 3. 巡逻路线
        patrol_names, patrol_routes = self._load_patrol_routes(map_name, map_dir)
        patrol_waypoints = patrol_routes[default_route_idx] if patrol_routes else []
        if patrol_routes:
            self._log(f"巡逻路线: {len(patrol_names)}条, "
                      f"默认 '{patrol_names[default_route_idx]}' ({len(patrol_waypoints)}途经点)")

        # 4. YOLO 模型
        self._status("加载 YOLO 模型...")
        from ultralytics import YOLO
        import config as cfg
        yolo_path = str(map_dir / "best.pt")
        yolo_model = YOLO(yolo_path)
        if hasattr(yolo_model, 'names'):
            cfg.CLASS_NAMES.clear()
            cfg.CLASS_NAMES.update(yolo_model.names)
        elif hasattr(yolo_model.model, 'names'):
            cfg.CLASS_NAMES.clear()
            cfg.CLASS_NAMES.update(yolo_model.model.names)
        self._log(f"YOLO: {len(cfg.CLASS_NAMES)}类  黑名单={cfg.NON_MONSTER_NAMES}")

        # 5. 角色模板
        self._status("截取角色名模板...")
        try:
            force_foreground(target_hwnd)
        except Exception:
            pass
        import time
        time.sleep(0.4)
        frame = capture_frame(target_hwnd)
        if frame is None:
            raise RuntimeError("截图失败，无法截取角色名模板")

        tx, ty, tr, tb = template_rect
        template = frame[ty:tb, tx:tr]
        frame_h, frame_w = frame.shape[:2]
        skip_px = int(frame_h * SEARCH_BOTTOM_SKIP_PCT)
        search_region = (0, 0, frame_w, frame_h - skip_px)
        self._log(f"模板: ({tx},{ty})->({tr},{tb})  skip={skip_px}px")

        try:
            force_foreground(target_hwnd)
        except Exception:
            pass
        time.sleep(0.3)

        return LoadResult(
            world_model=wm,
            yolo_model=yolo_model,
            template=template,
            search_region=search_region,
            map_cfg=map_cfg,
            patrol_route_names=patrol_names,
            patrol_all_routes=patrol_routes,
            patrol_waypoints=patrol_waypoints,
            mm_region=mm_region,
        )

    # ---- 巡逻路线解析 ----

    @staticmethod
    def _load_patrol_routes(map_name: str,
                             map_dir: Path) -> tuple[list[str], list[list[tuple[float, float]]]]:
        """从 markers.json 加载巡逻路线并解析为坐标。"""
        markers_path = map_dir / "markers.json"
        if not markers_path.exists():
            return [], []

        try:
            with open(markers_path, "r", encoding="utf-8") as f:
                md = json.load(f)
            mc = md.get(map_name, {}) if isinstance(md, dict) else {}
            routes = mc.get("patrol_routes", [])
            raw_platforms = mc.get("platforms", [])
            raw_ropes = mc.get("ropes", [])
            raw_jumps = mc.get("jumps", [])
            raw_flashes = mc.get("flash_points", [])
        except Exception:
            return [], []

        if not routes or not raw_platforms:
            return [], []

        anchor_map = MapLoader._build_anchor_map(
            raw_platforms, raw_ropes, raw_jumps, raw_flashes)

        names: list[str] = []
        all_coords: list[list[tuple[float, float]]] = []
        for route in routes:
            name = route.get("route_name", "未命名路线")
            coords: list[tuple[float, float]] = []
            for wid in route.get("waypoints", []):
                pt = anchor_map.get(wid)
                if pt:
                    coords.append(pt)
            if coords:
                names.append(name)
                all_coords.append(coords)
        return names, all_coords

    @staticmethod
    def _build_anchor_map(platforms: list[dict], ropes: list[dict],
                           jumps: list[dict], flashes: list[dict]
                           ) -> dict[str, tuple[float, float]]:
        """构建锚点ID → minimap坐标的映射表。

        注意：平台的 ID 分配规则为「avg_y 降序 → platform_0 / platform_1 ...」，
        与地图标记工具的 anchor_system.py 和 build_world_model.py 保持一致。
        """
        anchor_map: dict[str, tuple[float, float]] = {}

        # 平台：按 avg_y 降序排序后分配 ID
        sorted_plats: list[dict] = []
        for i, p in enumerate(platforms):
            np = dict(p)
            np["_idx"] = i
            sorted_plats.append(np)
        sorted_plats.sort(key=lambda p: p["avg_y"], reverse=True)
        for i, p in enumerate(sorted_plats):
            le = p["left_endpoint"]
            re = p["right_endpoint"]
            anchor_map[f"plat_{i}_L"] = (float(le["x"]), float(le["y"]))
            anchor_map[f"plat_{i}_R"] = (float(re["x"]), float(re["y"]))

        # 绳梯/跳跃/闪现：中点坐标
        for i, r in enumerate(ropes):
            tx, ty = r["top"]["x"], r["top"]["y"]
            bx, by = r["bottom"]["x"], r["bottom"]["y"]
            anchor_map[f"rope_{i}"] = ((tx + bx) / 2, (ty + by) / 2)

        for i, j in enumerate(jumps):
            fx, fy = j["from"]["x"], j["from"]["y"]
            tx, ty = j["to"]["x"], j["to"]["y"]
            anchor_map[f"jump_{i}"] = ((fx + tx) / 2, (fy + ty) / 2)

        for i, fl in enumerate(flashes):
            fx, fy = fl["from"]["x"], fl["from"]["y"]
            tx, ty = fl["to"]["x"], fl["to"]["y"]
            anchor_map[f"flash_{i}"] = ((fx + tx) / 2, (fy + ty) / 2)

        return anchor_map
