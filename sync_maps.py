#!/usr/bin/env python3
"""
地图数据同步脚本

将"地图标记工具"产出的数据同步到"自动脚本"的 maps/ 目录。

新结构: marker_output/{窗口名}/{地图名}_maps.json → maps/{窗口名}/{地图名}/markers.json
        marker_output/{窗口名}/{地图名}_model.json → maps/{窗口名}/{地图名}/world_model.json

用法:
    python sync_maps.py                        # 同步所有窗口所有地图
    python sync_maps.py 冒险岛怀旧服               # 同步指定窗口
    python sync_maps.py 冒险岛怀旧服 地铁一号线     # 同步指定窗口指定地图
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV_DIR = ROOT / "脚本开发工具"
MARKER_OUT = DEV_DIR / "地图标记工具" / "marker_output"
YOLO_MODELS = DEV_DIR / "YOLO训练工具" / "trained_models"
TARGET_DIR = ROOT / "自动脚本开发" / "maps"


def _safe(name: str) -> str:
    """Windows 非法字符替换。"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '_')
    return name


def discover_window_maps() -> dict[str, list[str]]:
    """扫描 marker_output，返回 {窗口名: [地图名列表]}"""
    result: dict[str, list[str]] = {}
    if not MARKER_OUT.is_dir():
        return result
    for win_dir in sorted(MARKER_OUT.iterdir()):
        if not win_dir.is_dir() or win_dir.name.startswith("."):
            continue
        maps = []
        for f in sorted(win_dir.iterdir()):
            if f.name.endswith("_maps.json"):
                map_name = f.name[:-len("_maps.json")]
                maps.append(map_name)
        if maps:
            result[win_dir.name] = maps
    return result


def sync_one(win_name: str, map_name: str) -> int:
    """同步单个窗口+地图。"""
    win_name = _safe(win_name)
    map_name = _safe(map_name)
    map_dir = TARGET_DIR / win_name / map_name
    map_dir.mkdir(parents=True, exist_ok=True)
    src_base = MARKER_OUT / win_name
    synced = 0

    # 1. maps.json → markers.json
    maps_src = src_base / f"{map_name}_maps.json"
    markers_dst = map_dir / "markers.json"
    if maps_src.exists():
        shutil.copy2(maps_src, markers_dst)
        synced += 1
        # 读取统计
        try:
            with open(maps_src, "r", encoding="utf-8") as f:
                mc = json.load(f)
            p = len(mc.get("platforms", [])); r = len(mc.get("ropes", []))
            j = len(mc.get("jumps", [])); fl = len(mc.get("flash_points", []))
            pr = len(mc.get("patrol_routes", []))
            print(f"   ✓ markers.json (平台{p} 绳梯{r} 跳跃{j} 闪现{fl} 路线{pr})")
        except Exception:
            print(f"   ✓ markers.json")
    else:
        print(f"   ⚠ {map_name}_maps.json 不存在")
        return synced

    # 2. _model.json → world_model.json
    model_src = src_base / f"{map_name}_model.json"
    model_dst = map_dir / "world_model.json"
    if model_src.exists():
        shutil.copy2(model_src, model_dst)
        synced += 1
        print(f"   ✓ world_model.json")
    else:
        print(f"   ⚠ {map_name}_model.json 不存在")

    # 3. best.pt
    pt_src = YOLO_MODELS / map_name / "best.pt"
    pt_dst = map_dir / "best.pt"
    if pt_src.exists():
        shutil.copy2(pt_src, pt_dst)
        synced += 1
        print(f"   ✓ best.pt")
    else:
        print(f"   - 无专属 best.pt")

    # 4. config.json（只创建，不覆盖）
    config_dst = map_dir / "config.json"
    if not config_dst.exists():
        try:
            with open(maps_src, "r", encoding="utf-8") as f:
                mc = json.load(f)
            mm_region = mc.get("mm_region", [])
        except Exception:
            mm_region = []
        with open(config_dst, "w", encoding="utf-8") as f:
            json.dump({"mm_region": mm_region}, f, ensure_ascii=False, indent=2)
        synced += 1
        print(f"   ✓ config.json (新建)")
    else:
        print(f"   - config.json (已存在)")

    return synced


def main():
    all_data = discover_window_maps()
    if not all_data:
        print("未找到任何地图数据")
        return

    args = sys.argv[1:]

    if len(args) >= 2:
        # 指定窗口+地图
        win_name, map_name = args[0], args[1]
        if win_name not in all_data or map_name not in all_data[win_name]:
            print(f"未找到 {win_name}/{map_name}")
            return
        print(f"[{win_name}/{map_name}]")
        total = sync_one(win_name, map_name)
    elif len(args) == 1:
        # 指定窗口
        win_name = args[0]
        if win_name not in all_data:
            print(f"未找到窗口: {win_name}")
            return
        total = 0
        for map_name in all_data[win_name]:
            print(f"[{win_name}/{map_name}]")
            total += sync_one(win_name, map_name)
    else:
        # 全部同步
        total = 0
        for win_name in sorted(all_data.keys()):
            for map_name in all_data[win_name]:
                print(f"[{win_name}/{map_name}]")
                total += sync_one(win_name, map_name)

    # 同步全局 system_setting.json
    ss_src = MARKER_OUT / "system_setting.json"
    ss_dst = TARGET_DIR / "system_setting.json"
    if ss_src.exists():
        shutil.copy2(ss_src, ss_dst)
        total += 1
        print(f"\n[全局] ✓ system_setting.json")

    print(f"\n完成！共同步 {total} 个文件。")


if __name__ == "__main__":
    main()
