# 巡逻路线回归方式功能 — 实施总结

## 概述
为固定路线巡逻功能添加了"回归方式"配置，控制当途经点所在平台与当前平台之间无直接连接时的行为。

## 三种回归方式

| 回归方式 | 行为 |
|---------|------|
| `无` | 通过世界模型 BFS 寻路，向上和向下都尝试找出口（原逻辑只向上寻路） |
| `一直走` | 朝目标途经点方向直线步行，不寻路 |
| `下跳` | 按住 Alt+↓ 0.5 秒，从当前平台跳下 |

## 修改的文件

### 1. `maps/射手训练场1/markers.json`
- 为"默认巡逻路线"添加 `"return_method": "一直走"` 字段

### 2. `map_loader.py`
- `LoadResult` 新增 `patrol_return_methods` 和 `patrol_return_method` 字段
- `_load_patrol_routes()` 返回值从 2 元组改为 3 元组，解析每条路线的 `return_method`
- 默认值：`"一直走"`（兼容未配置的旧路线）

### 3. `commands.py`
- 新增 `JumpDownCommand`：按住 `j`(Alt) + `d`(↓) 0.5 秒后释放
- `is_transition()` → `True`（下跳后触发过渡状态机等待落地）
- `decide()` 函数签名新增 `return_method` 参数

### 4. `decision_strategies.py`
- `DecisionStrategy.decide()` 抽象方法新增 `return_method` 参数
- `AutoHuntStrategy.decide()` 接受但忽略 `return_method`（自动寻怪不使用）
- `FixedRouteStrategy._cross_platform()` 核心改动：
  - 先查直达边（不变）
  - 无直达边时根据 `return_method` 分支：
    - `一直走` → `HoldDirCommand` 朝目标方向步行
    - `下跳` → `JumpDownCommand` Alt+↓
    - `无` → `find_nearest_exit()` BFS 双向寻路（上下都查）
  - 兜底：无可用出口时仍 `HoldDirCommand` 步行

### 5. `main.py`
- `AutoFarmV2App` 新增 `_patrol_return_method` 实例变量
- `start()` 从 `LoadResult` 读取并存储
- `_loop()` 调用 `decide()` 时传入 `return_method`
- `_on_patrol_mode_change()` 修复了调用 `MapLoader._load_patrol_routes` 的参数问题

## 数据流
```
markers.json (return_method) 
    → MapLoader._load_patrol_routes() 
    → LoadResult.patrol_return_method 
    → main.py._patrol_return_method 
    → decide(return_method=...) 
    → FixedRouteStrategy._cross_platform(return_method, ...)
```

## 向后兼容
- 旧路线未配置 `return_method` 时默认为 `"一直走"`，行为与修改前完全一致
- 自动寻怪模式不受影响（`AutoHuntStrategy` 忽略该参数）
