# 项目约定

## 通用开发规范
- 未明确要求时，不要做兼容处理。以自动脚本代码为标准，统一风格，不要预留中英文双值、多格式兜底等兼容逻辑。
- 不要主动操作 git（add/commit/push 等），除非用户明确要求。
- 截图（游戏窗口）时，先 BringToFront 提到最前，截完立刻放到最后面（不挡其他窗口）。
- 截图和临时文件不要主动删除，除非用户明确要求。
- 分析问题时要同时看决策日志和运行日志，不要只看决策日志。
- 修改代码前先分析清楚直接原因，不要凭推测改，改完要用日志验证。
- 每次修改代码后必须清理 `__pycache__/` 目录。

## 文件与路径
- 使用 Python 3.13 运行脚本：`C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`
- 所有源文件编码为 UTF-8。
- 文件路径使用 `pathlib.Path`，不要用字符串拼接。
- 地图数据从 `脚本开发工具/地图标记工具/marker_output/` 同步到 `自动脚本开发/maps/`，通过 `maps同步.bat` / `sync_maps.py`。

## 世界模型格式
- **rope 边**：使用 `top` / `bottom` 字段存储绳梯端点坐标。
- **jump/flash 边**：使用 `from_pt` / `to_pt` 字段存储跳跃/闪现端点坐标。
- 世界模型由 `model_generator.py`（标记工具）或 `build_world_model.py`（自动脚本）生成，两种工具输出的格式必须一致。

## 日志规范
- 动作日志（按键、移动、攻击等）使用 `_throttled_log()` 节流，相同消息每秒最多一条。
- 决策日志用时间戳 `── HH:MM:SS ──` 而非帧号作为标题。
- 运行日志每条加上 `[HH:MM:SS]` 时间戳前缀。
- 日志窗口只在滚动条在底部时自动滚到底，翻看历史时不跳回。
- 日志记录到文件（`run.log`）的同时输出到 UI。

## 配置持久化
- 所有 GUI 配置项统一保存到 `config_cache.json`，启动时通过 `_load_config()` 恢复。
- 新增配置项时在 `_save_config()` 加一行、在 `_restore_and_trace_config()` 加 `cache.get("key", default)` 恢复。
- 通过 `var.trace_add("write", _save)` 监听 tk 变量变更自动存盘，停止脚本时也保存。

## 朝向系统
- `state.facing` 追踪的是最后一次按键方向（由 `move()`/`turn()`/`jump()` 等设置），**不是游戏画面的实际朝向**。
- 固定路线步行用 `HoldDirCommand` → `move_no_facing()`，不更新 `state.facing`。
- 判断身前后怪物时使用 `state.facing`，需注意它可能与画面不同步。

## 地图标记工具
- 地图数据存于 `脚本开发工具/地图标记工具/marker_output/maps.json`。
- 世界模型存于 `脚本开发工具/地图标记工具/marker_output/{地图名}_model.json`。
- 编辑地图数据后在标记工具中重新生成模型，然后运行 `maps同步.bat` 同步到自动脚本。

## 自动脚本
- 启动时通过 `_detect_start_waypoint()` 自动匹配角色最近途经点，跳过已走过的。
- 固定路线决策优先级：绳梯 → 清怪（身前优先，身后转身）→ 跨平台 → 到达途经点 → 步行。
- 过渡命令（爬梯/跳跃/闪现）创建后设置 `transition.in_progress = True`，通过 `TransitionController` 管理生命周期。
- `ClimbCommand` 有完整的绳梯卡住检测（最近 10 帧位置不变 + |dx| < 2px → 恢复 0.5s）。
- 技能系统通过 `_get_effective_skill(mc)` 根据释放规则和怪物数返回技能 info dict。
- 调试截图默认打开，勾选后每 tick 保存到 `debug_frames/`，`frame_00~19.png` 循环覆盖。
