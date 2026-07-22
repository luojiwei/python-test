#!/usr/bin/env python3
"""
地图数据同步脚本

将"地图标记工具"产出的数据同步到"自动脚本"的 maps/ 目录。

同步内容:
  - world_model.json  ←  {map}_model.json
  - markers.json       ←  maps.json（提取该地图数据 + patrol_routes）
  - best.pt            ←  YOLO训练工具/trained_models/{map}/best.pt
  - config.json        ←  保留已有（如不存在则提示手动创建）

用法:
    python sync_maps.py              # 同步所有地图
    python sync_maps.py 射手训练场1    # 同步指定地图
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEV_DIR = ROOT / "脚本开发工具"
MARKER_OUT = DEV_DIR / "地图标记工具" / "marker_output"
MAPS_JSON = MARKER_OUT / "maps.json"
YOLO_MODELS = DEV_DIR / "YOLO训练工具" / "trained_models"
TARGET_DIR = ROOT / "自动脚本开发" / "maps"


def load_maps_json() -> dict:
    if not MAPS_JSON.exists():
        print(f"[错误] maps.json 不存在: {MAPS_JSON}")
        return {}
    with open(MAPS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def sync_map(map_name: str, maps_data: dict) -> int:
    """同步单个地图，返回同步的文件数。"""
    map_dir = TARGET_DIR / map_name
    map_dir.mkdir(parents=True, exist_ok=True)
    synced = 0

    map_cfg = maps_data.get(map_name, {})

    # 1. world_model.json（始终覆盖）
    model_src = MARKER_OUT / f"{map_name}_model.json"
    model_dst = map_dir / "world_model.json"
    if model_src.exists():
        shutil.copy2(model_src, model_dst)
        synced += 1
        print(f"   ✓ world_model.json")
    else:
        print(f"   ⚠ {map_name}_model.json 不存在，跳过")

    # 2. markers.json（始终从 maps.json 重建，patrol_routes 以源数据为准）
    markers_dst = map_dir / "markers.json"
    markers_data: dict = {map_name: {}}
    for key in ("platforms", "ropes", "jumps", "flash_points", "patrol_routes",
                 "mm_region", "minimap_size"):
        if key in map_cfg:
            markers_data[map_name][key] = map_cfg[key]

    with open(markers_dst, "w", encoding="utf-8") as f:
        json.dump(markers_data, f, ensure_ascii=False, indent=2)
    synced += 1
    print(f"   ✓ markers.json (平台{len(markers_data[map_name].get('platforms',[]))}"
          f" 绳梯{len(markers_data[map_name].get('ropes',[]))}"
          f" 跳跃{len(markers_data[map_name].get('jumps',[]))}"
          f" 闪现{len(markers_data[map_name].get('flash_points',[]))}"
          f" 路线{len(markers_data[map_name].get('patrol_routes',[]))})")

    # 3. best.pt（始终覆盖）
    pt_src = YOLO_MODELS / map_name / "best.pt"
    pt_dst = map_dir / "best.pt"
    if pt_src.exists():
        shutil.copy2(pt_src, pt_dst)
        synced += 1
        print(f"   ✓ best.pt")
    else:
        print(f"   ⚠ best.pt 不存在: {pt_src}")

    # 4. config.json（只创建，不覆盖已有的）
    config_dst = map_dir / "config.json"
    if not config_dst.exists():
        mm_region = map_cfg.get("mm_region", [])
        config = {"template_rect": [85, 728, 150, 745], "mm_region": mm_region}
        with open(config_dst, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        synced += 1
        print(f"   ✓ config.json (新建，请手动确认 template_rect)")
    else:
        print(f"   - config.json (已存在，跳过)")

    return synced


def main():
    maps_data = load_maps_json()
    if not maps_data:
        print("无法加载 maps.json，退出。")
        return

    if len(sys.argv) >= 2:
        map_names = [sys.argv[1]]
    else:
        map_names = list(maps_data.keys())

    total = 0
    for name in map_names:
        if name not in maps_data:
            print(f"[{name}] 不在 maps.json 中，跳过")
            continue
        print(f"\n[{name}]")
        total += sync_map(name, maps_data)

    print(f"\n完成！共同步 {total} 个文件。")


if __name__ == "__main__":
    main()
