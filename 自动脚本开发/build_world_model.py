#!/usr/bin/env python3
"""
从 maps.json 生成完整世界模型 (world_model.json)

职责：
  1. 为平台分配稳定 ID（按小地图 Y 从下到上 = platform_0, platform_1, ...）
  2. 计算每条绳梯连接了哪两个平台
  3. 计算每个传送点连接了哪两个平台，补全双向边
  4. 建立邻接表，供 BFS 寻路使用
  5. 为每个平台记录出口列表（绳梯 / 传送点），标注在小地图上的位置
"""

from edge_types import EdgeType

import json
import sys
from pathlib import Path
from collections import defaultdict


# ---------- 数据加载 ----------

def load_maps(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- 平台 ID 分配 ----------

def assign_platform_ids(map_data: dict) -> list[dict]:
    """
    按 avg_y 从小到大排序，分配 stable_id = "platform_{i}"
    返回排序后的平台列表（每个 dict 里新增 stable_id, y_range 字段）
    """
    platforms = map_data.get("platforms", [])
    # 深拷贝避免污染原数据
    result: list[dict] = []
    for i, p in enumerate(platforms):
        new_p = dict(p)
        new_p["_index"] = i  # 原数组索引，仅调试用
        result.append(new_p)

    # 按 avg_y 升序（avg_y 越小 = 越靠小地图顶部 = 游戏里越高，但小地图坐标系 y↓）
    # 这里用 avg_y 从小到大排序，0 号 = 小地图上 y 最小的 = 游戏里最高的平台
    # 但实际上底层平台在小地图下方（y 更大），所以应该反过来：
    # 即 avg_y 越大 → 越底层 → ID 越小
    result.sort(key=lambda p: p["avg_y"], reverse=True)  # 底层在前

    for i, p in enumerate(result):
        p["id"] = f"platform_{i}"
        p["y_range"] = [p["min_y"], p["max_y"]]

    return result


# ---------- 点匹配平台 ----------

def find_platform_for_point(
    px: float, py: float,
    platforms: list[dict],
    x_tolerance: int = 6,
    y_tolerance: int = 4,
    direction_hint: str | None = None,
    source_avg_y: float | None = None,
    exclude_ids: set[str] | None = None,
) -> str | None:
    """
    判断小地图坐标 (px, py) 落在哪个平台上。
    匹配条件：X 在 [left_x - x_tol, right_x + x_tol]，
            Y 在 [min_y - y_tol, max_y + y_tol]。

    如果有 direction_hint，优先选择符合方向约束的平台：
      "up"   → 目标平台 avg_y < 来源 avg_y（小地图 y↓ = 游戏里 y↑）
      "down" → 目标平台 avg_y > 来源 avg_y
    """
    candidates: list[tuple[str, float]] = []

    for p in platforms:
        if exclude_ids and p["id"] in exclude_ids:
            continue
        lx = p["left_endpoint"]["x"] - x_tolerance
        rx = p["right_endpoint"]["x"] + x_tolerance
        if not (lx <= px <= rx):
            continue

        py_min = p["min_y"] - y_tolerance
        py_max = p["max_y"] + y_tolerance
        if not (py_min <= py <= py_max):
            continue

        candidates.append((p["id"], p["avg_y"], p))

    if not candidates:
        return None

    # 方向约束筛选
    if direction_hint and source_avg_y is not None and len(candidates) > 1:
        filtered = []
        for pid, avg_y, p in candidates:
            if direction_hint == "up" and avg_y < source_avg_y:
                filtered.append((pid, avg_y, p))
            elif direction_hint == "down" and avg_y > source_avg_y:
                filtered.append((pid, avg_y, p))
        if filtered:
            candidates = filtered

    # 边界点就近匹配：Y 距离优先，Y 相等时选 X 中心更近的
    if len(candidates) == 1:
        return candidates[0][0]

    best_pid: str | None = None
    best_score: float = float("inf")
    for pid, avg_y, p in candidates:
        y_dist = abs(py - avg_y)
        cx = (p["left_endpoint"]["x"] + p["right_endpoint"]["x"]) / 2
        x_dist = abs(px - cx)
        # Y 权重 > X 权重（平台层次比水平位置更重要）
        score = y_dist * 10 + x_dist
        if score < best_score:
            best_score = score
            best_pid = pid

    return best_pid


# ---------- 绳梯 → 平台连接 ----------

def compute_rope_edges(
    ropes: list[dict],
    platforms: list[dict],
) -> list[dict]:
    """
    为每条绳梯匹配 top / bottom 分别属于哪个平台。
    返回 edge 列表，每个 edge = {
        type: "rope",
        from_platform: str,
        to_platform: str,
        from_direction: "up" | "down",   # 从 from_platform 往哪个方向走
        top: {x, y},
        bottom: {x, y},
        minimap_x: int,                   # 绳梯在小地图上的 X 坐标（用于对齐）
    }
    """
    edges: list[dict] = []
    for r in ropes:
        top_x, top_y = r["top"]["x"], r["top"]["y"]
        bot_x, bot_y = r["bottom"]["x"], r["bottom"]["y"]

        plat_top = find_platform_for_point(top_x, top_y, platforms)
        plat_bot = find_platform_for_point(bot_x, bot_y, platforms)

        if plat_top is None or plat_bot is None:
            print(f"  ⚠ 绳梯 ({top_x},{top_y})→({bot_x},{bot_y}) 无法匹配平台 "
                  f"(top={plat_top}, bot={plat_bot})，跳过")
            continue

        # top 在小地图上方（y 更小），对应游戏里更高层
        # 加入两条有向边
        edges.append({
            "type": "rope",
            "from_platform": plat_bot,
            "to_platform": plat_top,
            "direction": "up",
            "top": {"x": top_x, "y": top_y},
            "bottom": {"x": bot_x, "y": bot_y},
            "minimap_x": bot_x,
        })
        edges.append({
            "type": "rope",
            "from_platform": plat_top,
            "to_platform": plat_bot,
            "direction": "down",
            "top": {"x": top_x, "y": top_y},
            "bottom": {"x": bot_x, "y": bot_y},
            "minimap_x": bot_x,
        })

    return edges


# ---------- 跳跃点 → 平台连接（始终双向） ----------

def compute_jump_edges(
    jumps: list[dict],
    platforms: list[dict],
) -> list[dict]:
    """跳跃点：from↔to 双向可达，无需方向标记"""
    edges: list[dict] = []
    for jp in jumps:
        fx, fy = jp["from"]["x"], jp["from"]["y"]
        tx, ty = jp["to"]["x"], jp["to"]["y"]

        plat_from = find_platform_for_point(fx, fy, platforms)
        plat_to = find_platform_for_point(tx, ty, platforms)

        if plat_from is None or plat_to is None:
            print(f"  ⚠ 跳跃点 ({fx},{fy})↔({tx},{ty}) 无法匹配平台 "
                  f"(from={plat_from}, to={plat_to})，跳过")
            continue

        if plat_from == plat_to:
            # 自环：重新匹配，排除当前平台
            plat_to_alt = find_platform_for_point(tx, ty, platforms, exclude_ids={plat_from})
            if plat_to_alt:
                print(f"  ✓ 跳跃点 ({fx},{fy})↔({tx},{ty}) 自环→修正: "
                      f"{plat_from}↔{plat_to_alt}")
                plat_to = plat_to_alt
            else:
                plat_from_alt = find_platform_for_point(fx, fy, platforms, exclude_ids={plat_to})
                if plat_from_alt:
                    print(f"  ✓ 跳跃点 ({fx},{fy})↔({tx},{ty}) 自环→修正: "
                          f"{plat_from_alt}↔{plat_to}")
                    plat_from = plat_from_alt
                else:
                    print(f"  ℹ 跳跃点 ({fx},{fy})↔({tx},{ty}) 在同一平台 {plat_from}，跳过")
                    continue

        # 双向
        edges.append({
            "type": EdgeType.JUMP,
            "from_platform": plat_from,
            "to_platform": plat_to,
            "from": {"x": fx, "y": fy},
            "to": {"x": tx, "y": ty},
        })
        edges.append({
            "type": EdgeType.JUMP,
            "from_platform": plat_to,
            "to_platform": plat_from,
            "from": {"x": tx, "y": ty},
            "to": {"x": fx, "y": fy},
        })

    return edges


# ---------- 闪现点 → 平台连接（可有向） ----------

def compute_flash_edges(
    flashes: list[dict],
    platforms: list[dict],
) -> list[dict]:
    """闪现点：type=one_way 只加正向，type=two_way 加双向"""
    edges: list[dict] = []
    for fl in flashes:
        fx, fy = fl["from"]["x"], fl["from"]["y"]
        tx, ty = fl["to"]["x"], fl["to"]["y"]
        flash_type = fl.get("type", "two_way")

        plat_from = find_platform_for_point(fx, fy, platforms)
        plat_to = find_platform_for_point(tx, ty, platforms)

        if plat_from is None or plat_to is None:
            print(f"  ⚠ 闪现点 ({fx},{fy})→({tx},{ty}) 无法匹配平台 "
                  f"(from={plat_from}, to={plat_to})，跳过")
            continue

        if plat_from == plat_to:
            print(f"  ℹ 闪现点 ({fx},{fy})→({tx},{ty}) 在同一平台 {plat_from}，跳过")
            continue

        edges.append({
            "type": EdgeType.FLASH,
            "from_platform": plat_from,
            "to_platform": plat_to,
            "flash_type": flash_type,
            "from": {"x": fx, "y": fy},
            "to": {"x": tx, "y": ty},
        })

        if flash_type == "two_way":
            edges.append({
                "type": EdgeType.FLASH,
                "from_platform": plat_to,
                "to_platform": plat_from,
                "flash_type": flash_type,
                "from": {"x": tx, "y": ty},
                "to": {"x": fx, "y": fy},
            })

    return edges


# ---------- 邻接表 + 出口列表 ----------

def build_adjacency(
    platforms: list[dict],
    edges: list[dict],
) -> tuple[dict, dict]:
    """
    返回:
      adjacency: {platform_id: [edge, ...]}   — 从该平台出发的所有边
      exits: {platform_id: list[dict]}         — 按类型分组的出口
    """
    adjacency: dict[str, list[dict]] = defaultdict(list)
    exits: dict[str, dict] = {}

    for p in platforms:
        pid = p["id"]
        exits[pid] = {"ropes": [], "jumps": [], "flashes": []}

    for edge in edges:
        src = edge["from_platform"]
        adjacency[src].append(edge)

        if edge["type"] == EdgeType.ROPE:
            exits[src]["ropes"].append({
                "direction": edge["direction"],
                "minimap_x": edge["minimap_x"],
                "target_platform": edge["to_platform"],
            })
        elif edge["type"] == EdgeType.JUMP:
            exits[src]["jumps"].append({
                "from": edge["from"],
                "to": edge["to"],
                "target_platform": edge["to_platform"],
            })
        elif edge["type"] == "flash":
            exits[src]["flashes"].append({
                "flash_type": edge["flash_type"],
                "from": edge["from"],
                "to": edge["to"],
                "target_platform": edge["to_platform"],
            })

    return dict(adjacency), exits


# ---------- 主流程 ----------

def build_world_model(maps_path: str, output_path: str, map_name: str | None = None) -> None:
    raw = load_maps(maps_path)

    # 支持指定地图名，否则用第一张
    if map_name:
        if map_name not in raw:
            print(f"错误: 地图 '{map_name}' 不在 {maps_path} 中")
            print(f"可用地图: {list(raw.keys())}")
            sys.exit(1)
        map_data = raw[map_name]
    else:
        map_name = list(raw.keys())[0]
        map_data = raw[map_name]

    print(f"地图: {map_name}")
    print(f"  原始平台数: {len(map_data.get('platforms', []))}")
    print(f"  绳梯数:     {len(map_data.get('ropes', []))}")
    print(f"  跳跃点数:   {len(map_data.get('jumps', []))}")
    print(f"  闪现点数:   {len(map_data.get('flash_points', []))}")

    # 1. 平台排序 + ID
    platforms = assign_platform_ids(map_data)
    print(f"\n平台列表 (从下到上):")
    for p in platforms:
        print(f"  {p['id']:>12s}  avg_y={p['avg_y']:>4d}  "
              f"y_range=[{p['min_y']:>3d}, {p['max_y']:>3d}]  "
              f"x_range=[{p['left_endpoint']['x']:>3d}, {p['right_endpoint']['x']:>3d}]")

    # 2. 绳梯边
    rope_edges = compute_rope_edges(map_data.get("ropes", []), platforms)
    print(f"\n绳梯边: {len(rope_edges)} 条（含双向）")
    for e in rope_edges:
        print(f"  {e['from_platform']} --[{e['direction']}]--> {e['to_platform']}  "
              f"(x={e['minimap_x']})")

    # 3. 跳跃点边
    jump_edges = compute_jump_edges(map_data.get("jumps", []), platforms)
    print(f"\n跳跃边: {len(jump_edges)} 条（含双向）")
    for e in jump_edges:
        print(f"  {e['from_platform']} <--jump--> {e['to_platform']}  "
              f"({e['from']['x']},{e['from']['y']})↔({e['to']['x']},{e['to']['y']})")

    # 4. 闪现点边
    flash_edges = compute_flash_edges(map_data.get("flashes", []), platforms)
    print(f"\n闪现边: {len(flash_edges)} 条")
    for e in flash_edges:
        arrow = "<-->" if e["flash_type"] == "two_way" else "-->"
        print(f"  {e['from_platform']} {arrow} {e['to_platform']}  ({e['flash_type']})  "
              f"({e['from']['x']},{e['from']['y']})→({e['to']['x']},{e['to']['y']})")

    # 5. 邻接表
    all_edges = rope_edges + jump_edges + flash_edges
    adjacency, exits = build_adjacency(platforms, all_edges)

    print(f"\n邻接表:")
    for pid in sorted(adjacency.keys(), key=lambda k: int(k.split("_")[1])):
        neighbors = adjacency[pid]
        if not neighbors:
            print(f"  {pid}: (孤立平台)")
        else:
            for n in neighbors:
                detail = ""
                if n["type"] == EdgeType.ROPE:
                    detail = f":{n['direction']}"
                elif n["type"] == EdgeType.FLASH:
                    detail = f":{n['flash_type']}"
                print(f"  {pid} --[{n['type']}{detail}]--> {n['to_platform']}")

    # 5. 出口汇总
    print(f"\n各平台出口:")
    for pid in sorted(exits.keys(), key=lambda k: int(k.split("_")[1])):
        ex = exits[pid]
        rope_dirs = [r.get("direction", "?") for r in ex["ropes"]]
        jump_count = len(ex["jumps"])
        flash_count = len(ex["flashes"])
        print(f"  {pid}: 绳梯={rope_dirs}  跳跃={jump_count}  闪现={flash_count}")

    # 6. 构建输出
    world_model = {
        "map_name": map_name,
        "minimap_size": map_data.get("minimap_size"),
        "mm_region": map_data.get("mm_region"),
        "platforms": platforms,
        "edges": all_edges,
        "adjacency": adjacency,
        "exits": exits,
        "platform_order_bottom_to_top": [p["id"] for p in platforms],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(world_model, f, ensure_ascii=False, indent=2)

    # 7. 连通性检查（BFS）
    from collections import deque
    visited: set[str] = set()
    if platforms:
        start = platforms[0]["id"]
        q: deque[str] = deque([start])
        while q:
            node = q.popleft()
            if node in visited:
                continue
            visited.add(node)
            for edge in adjacency.get(node, []):
                if edge["to_platform"] not in visited:
                    q.append(edge["to_platform"])
    isolated = [p["id"] for p in platforms if p["id"] not in visited]
    print(f"\n✓ 世界模型已写入: {output_path}")
    print(f"  平台数: {len(platforms)}")
    print(f"  边数:   {len(all_edges)}")
    print(f"  连通平台: {len(visited)}/{len(platforms)}")
    if isolated:
        print(f"  ⚠ 孤立平台（无法从底层到达）: {isolated}")
    else:
        print(f"  ✓ 所有平台连通")


if __name__ == "__main__":
    import os
    # 支持命令行参数: python build_world_model.py <map_name> [input_path] [output_path]
    if len(sys.argv) >= 2:
        map_name = sys.argv[1]
        maps_path = sys.argv[2] if len(sys.argv) >= 3 else \
            r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\脚本开发工具\地图标记工具\marker_output\maps.json"
        output_path = sys.argv[3] if len(sys.argv) >= 4 else \
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "maps", map_name, "world_model.json")
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        build_world_model(maps_path, output_path, map_name=map_name)
    else:
        maps_path = r"D:\Program Files (x86)\Tencent\WorkBuddy\python-test\脚本开发工具\地图标记工具\marker_output\maps.json"
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_model.json")
        build_world_model(maps_path, output_path)
