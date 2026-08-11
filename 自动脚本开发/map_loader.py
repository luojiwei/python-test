"""map_loader.py — 地图资源统一加载器。

将 start() 中 ~120 行的分散加载逻辑集中为一个 MapLoader 类。
负责：配置解析 → 世界模型 → 巡逻路线 → YOLO → 角色模板。
返回 LoadResult dataclass，main.py 只需一行 loader.load(name)。
"""

import ctypes
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from config import PROJECT_DIR, SEARCH_BOTTOM_SKIP_PCT, validate_map_resources


def _safe(name: str) -> str:
    """Windows 非法字符替换。"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    return name
from input_utils import capture_frame, force_foreground
from perception import move_model_to_device
from world_model import WorldModel, load_world_model


@dataclass
class LoadResult:
    """MapLoader 的一次性加载结果。"""
    world_model: WorldModel
    yolo_model: object          # ultralytics.YOLO
    template: np.ndarray        # 角色名模板图像
    search_region: tuple[int, int, int, int]  # (x, y, w, h)
    map_cfg: dict               # config.json 原始数据
    dot_hsv_lower: np.ndarray = field(default_factory=lambda: np.array([25, 100, 180]))
    dot_hsv_upper: np.ndarray = field(default_factory=lambda: np.array([35, 255, 255]))
    hp_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    mp_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    patrol_route_names: list[str] = field(default_factory=list)
    patrol_all_routes: list[list[tuple[float, float]]] = field(default_factory=list)
    patrol_waypoints: list[tuple[float, float]] = field(default_factory=list)
    patrol_return_methods: list[str] = field(default_factory=list)
    patrol_return_method: str = "一直走"
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

        map_dir = PROJECT_DIR / "maps"

        # 获取窗口标题，按窗口名加载资源
        win_title = ""
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(target_hwnd, buf, 256)
            win_title = buf.value.strip()
        except Exception:
            pass

        # 窗口级地图目录: maps/{窗口名}/{地图名}/
        if win_title:
            map_dir = map_dir / _safe(win_title)
        map_dir = map_dir / _safe(map_name)

        # 1. 配置
        config_path = map_dir / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            map_cfg = json.load(f)

        template_rect = MapLoader._resolve_template_rect(map_dir, target_hwnd)
        mm_region = tuple(map_cfg.get("mm_region", [8, 97, 128, 208]))

        # 黄点 HSV（从 system_setting.json 按窗口名读取）
        dot_lower, dot_upper, hp_rect, mp_rect, marked_search_region = MapLoader._resolve_system_settings(map_dir, target_hwnd)

        # 2. 世界模型
        self._status(f"加载世界模型 [{map_name}]...")
        wm_path = str(map_dir / "world_model.json")
        wm = load_world_model(wm_path)
        wm.mm_region = list(mm_region)
        self._log(f"世界模型: {len(wm.platforms)} 平台, {len(wm.edges)} 边")

        # 3. 巡逻路线
        patrol_names, patrol_routes, patrol_return_methods = self._load_patrol_routes(map_name, map_dir)
        patrol_waypoints = patrol_routes[default_route_idx] if patrol_routes else []
        patrol_return_method = patrol_return_methods[default_route_idx] if patrol_return_methods else "一直走"
        if patrol_routes:
            self._log(f"巡逻路线: {len(patrol_names)}条, "
                      f"默认 '{patrol_names[default_route_idx]}' ({len(patrol_waypoints)}途经点) "
                      f"回归={patrol_return_method}")

        # 4. YOLO 模型（优先地图专属，无则用兜底）
        self._status("加载 YOLO 模型...")
        from ultralytics import YOLO
        import config as cfg
        yolo_path = map_dir / "best.pt"
        fallback_path = map_dir.parent.parent / "best.pt"
        if not yolo_path.exists():
            if fallback_path.exists():
                yolo_path = fallback_path
                self._log(f"YOLO: 使用兜底模型 ({yolo_path.name})")
            else:
                raise FileNotFoundError(f"YOLO 模型不存在: {yolo_path}\n兜底模型也不存在: {fallback_path}")
        else:
            self._log(f"YOLO: 使用专属模型 ({map_name})")
        yolo_model = YOLO(str(yolo_path))
        if hasattr(yolo_model, 'names'):
            cfg.CLASS_NAMES.clear()
            cfg.CLASS_NAMES.update(yolo_model.names)
        elif hasattr(yolo_model.model, 'names'):
            cfg.CLASS_NAMES.clear()
            cfg.CLASS_NAMES.update(yolo_model.model.names)
        self._log(f"YOLO: {len(cfg.CLASS_NAMES)}类  黑名单={cfg.NON_MONSTER_NAMES}")
        move_model_to_device(yolo_model)

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
        # 边界检查：坐标必须在帧范围内
        frame_h, frame_w = frame.shape[:2]
        if ty < 0 or tb > frame_h or tx < 0 or tr > frame_w or tb <= ty or tr <= tx:
            self._log(f"模板坐标越界 rect={list(template_rect)} frame=({frame_w},{frame_h})，回退默认值")
            tx, ty, tr, tb = (85, 728, 150, 745)
            template_rect = (tx, ty, tr, tb)
        template = frame[ty:tb, tx:tr]
        th, tw = template.shape[:2]
        if th <= 0 or tw <= 0:
            self._log(f"模板区域为空 rect={list(template_rect)} frame=({frame_w},{frame_h})，回退默认值")
            tx, ty, tr, tb = (85, 728, 150, 745)
            template_rect = (tx, ty, tr, tb)
            template = frame[ty:tb, tx:tr]
        # 搜索范围：优先用标注值，兜底计算值
        skip_px = int(frame_h * SEARCH_BOTTOM_SKIP_PCT)
        if marked_search_region:
            search_region = marked_search_region
            self._log(f"搜索范围(标注): ({search_region[0]},{search_region[1]})-({search_region[2]},{search_region[3]})")
        else:
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
            dot_hsv_lower=dot_lower,
            dot_hsv_upper=dot_upper,
            hp_rect=hp_rect,
            mp_rect=mp_rect,
            patrol_route_names=patrol_names,
            patrol_all_routes=patrol_routes,
            patrol_waypoints=patrol_waypoints,
            patrol_return_methods=patrol_return_methods,
            patrol_return_method=patrol_return_method,
            mm_region=mm_region,
        )

    @staticmethod
    def _resolve_template_rect(map_dir: Path,
                                target_hwnd: int) -> tuple[int, int, int, int]:
        """按窗口名从 system_setting.json 读取模板区域。

        数据格式（新）: {窗口标题: {template_rect: [x1,y1,x2,y2], window_size: [w,h]}}
        兼容旧格式:    {窗口标题: [x1,y1,x2,y2]}
        优先级: system_setting.json[窗口标题].template_rect
                > system_setting.json["_default"].template_rect
                > 回退默认值 [85, 728, 150, 745]
        """
        default_rect = (85, 728, 150, 745)

        ss_path = map_dir.parent.parent / "system_setting.json"
        if not ss_path.exists():
            return default_rect

        try:
            with open(ss_path, "r", encoding="utf-8") as f:
                ss_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return default_rect

        # 获取窗口标题
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(target_hwnd, buf, 256)
            win_title = buf.value.strip()
        except Exception:
            win_title = ""

        def _extract_rect(entry) -> list | None:
            if isinstance(entry, dict):
                return entry.get("template_rect")
            if isinstance(entry, list) and len(entry) == 4:
                return entry
            return None

        # 按窗口名查找
        if win_title and win_title in ss_data:
            rect = _extract_rect(ss_data[win_title])
            if rect:
                return tuple(rect)
        # 回退到 _default
        if "_default" in ss_data:
            rect = _extract_rect(ss_data["_default"])
            if rect:
                return tuple(rect)
        return default_rect

    @staticmethod
    def _resolve_system_settings(map_dir: Path,
                                  target_hwnd: int) -> tuple:
        """按窗口名从 system_setting.json 读取所有系统设置。

        Returns:
            (dot_hsv_lower, dot_hsv_upper, hp_rect, mp_rect)
        """
        default_lower = np.array([25, 100, 180])
        default_upper = np.array([35, 255, 255])

        ss_path = map_dir.parent.parent / "system_setting.json"
        if not ss_path.exists():
            return default_lower, default_upper, (0,0,0,0), (0,0,0,0), []

        try:
            with open(ss_path, "r", encoding="utf-8") as f:
                ss_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return default_lower, default_upper, (0,0,0,0), (0,0,0,0), []

        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(target_hwnd, buf, 256)
            win_title = buf.value.strip()
        except Exception:
            win_title = ""

        def _extract(key, default):
            entry = ss_data.get(win_title) or ss_data.get("_default")
            if isinstance(entry, dict):
                val = entry.get(key)
                if isinstance(val, list) and len(val) >= 2:
                    return tuple(val) if isinstance(default, tuple) else np.array(val)
            return default

        lower = _extract("dot_hsv_lower", default_lower)
        upper = _extract("dot_hsv_upper", default_upper)
        hp = tuple(_extract("hp_rect", [0, 0, 0, 0]))
        mp = tuple(_extract("mp_rect", [0, 0, 0, 0]))
        sr = _extract("search_region", [])
        search_region = tuple(sr) if isinstance(sr, list) and len(sr) == 4 else []
        return lower, upper, hp, mp, search_region

    # ---- 巡逻路线解析 ----

    @staticmethod
    def _load_patrol_routes(map_name: str,
                             map_dir: Path) -> tuple[list[str], list[list[tuple[float, float]]], list[str]]:
        """从 markers.json 加载巡逻路线并解析为坐标。

        Returns:
            (route_names, all_coords, return_methods)
        """
        markers_path = map_dir / "markers.json"
        if not markers_path.exists():
            return [], []

        try:
            with open(markers_path, "r", encoding="utf-8") as f:
                md = json.load(f)
            mc = md.get(map_name, {}) if isinstance(md, dict) and map_name in md else md
            routes = mc.get("patrol_routes", [])
            raw_platforms = mc.get("platforms", [])
            raw_ropes = mc.get("ropes", [])
            raw_jumps = mc.get("jumps", [])
            raw_flashes = mc.get("flash_points", [])
        except Exception:
            return [], [], []

        if not routes or not raw_platforms:
            return [], [], []

        anchor_map = MapLoader._build_anchor_map(
            raw_platforms, raw_ropes, raw_jumps, raw_flashes)

        names: list[str] = []
        all_coords: list[list[tuple[float, float]]] = []
        return_methods: list[str] = []
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
                return_methods.append(route.get("return_method", "一直走"))
        return names, all_coords, return_methods

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
