# 项目约定

## 通用开发规范
- 未明确要求时，不要做兼容处理。以自动脚本代码为标准，统一风格，不要预留中英文双值、多格式兜底等兼容逻辑。
- 不要主动操作 git（add/commit/push 等），除非用户明确要求。
- 截图（游戏窗口）时，先 BringToFront 提到最前，截完立刻放到最后面（不挡其他窗口）。截图顺序：前置顶 → 截全帧 → 截所有子区域 → 后置底 → 分析（截完后窗口立刻让位）。
- 截图和临时文件不要主动删除，除非用户明确要求。
- 分析问题时要同时看决策日志和运行日志，不要只看决策日志。
- 修改代码前先分析清楚直接原因，不要凭推测改，改完要用日志验证。
- 每次修改代码后必须清理 `__pycache__/` 目录。

## OpenCV 5.x 中文路径问题
- `cv2.imwrite("含中文路径/...", img)` 会静默返回 False。
- 兜底: `cv2.imencode(".png", img)` + `Path.write_bytes(buf.tobytes())`。

## 截图首选 PrintWindow（不被遮挡）
- 用 `ctypes` 调用 `user32.GetDC` + `gdi32.CreateCompatibleDC/Bitmap` + `user32.PrintWindow(hwnd, dc, 1)` 可后台截取目标窗口内容。
- `mss.grab()` 按屏幕坐标抓取，会被其他窗口遮挡（WorkBuddy 容易盖住游戏）。开发/测试脚本**优先用 PrintWindow**。
- 实现见 `自动脚本开发/input_utils.py:_capture_window()`。
- 注意: `ctypes.wintypes` 没有 `BITMAPINFO` 类，需自己 `ctypes.Structure` 定义 `BITMAPINFOHEADER`。
- PW_CLIENTONLY=1 截取客户端区域（不含标题栏），**窗口尺寸必须用 GetClientRect，不能用 GetWindowRect**。
- **DPI 感知**: PrintWindow 截图必须用 `_dpi_unaware()` 上下文临时把线程切到 DPI_UNAWARE，否则 DPI-aware 线程（如 Qt）下 GetClientRect 返回物理像素但 PrintWindow 按逻辑像素绘制，只有左上角有内容。三个工具均需遵守：`自动脚本开发/input_utils.py`、`脚本开发工具/地图标记工具/window_utils.py`、`脚本开发工具/YOLO自动标注工具/utils.py`。

## 文件与路径
- 使用 Python 3.13 运行脚本：`C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`
- 所有源文件编码为 UTF-8。
- **`.bat` 必须用 CRLF 换行**（UTF-8 无 BOM + 第二行 `chcp 65001 >nul`，参照 `maps同步.bat`）。Write 工具默认落 LF，cmd.exe 按字节偏移解析批处理会错行，表现为 `'p0"' 不是内部或外部命令`、中文变 `笉瀛樺湪` 等乱码。写 bat 用 `Path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))`。
- 文件路径使用 `pathlib.Path`，不要用字符串拼接。
- 地图数据从 `脚本开发工具/地图标记工具/marker_output/` 同步到 `自动脚本开发/maps/`，通过 `maps同步.bat` / `sync_maps.py`。
- 本机 Git Bash shim 缺 `head`/`tail`/`dirname` 等基础命令（管道里会报 `command not found`、退出码 127），`wmic` 被安全策略拉黑。统计/切片用 Python 读取，列进程用 PowerShell `Get-CimInstance Win32_Process`（**不能出现 `%VAR%` 写法**，会被判为 cmd 语法而拦截）或 `tasklist`。
- 独立小工具放各自的顶层目录，不依赖 `自动脚本开发/` 和 `脚本开发工具/`（如 `循环按键脚本/`：main.py + 启动.bat）。

## GUI 工具与 tkinter（Python 解释器选择）
- 托管 Python 3.13.12（`binaries\python\versions\3.13.12`）**不含 tkinter**：无 `Lib/tkinter`、无 `DLLs/_tkinter.pyd`、无 `tcl/`。
- 四个 venv（`envs\auto-farm`、`default`、`gdino`、`yolo`）都继承自它，同样**没有 tkinter**（yolo 也没有 PySide6）。
- 唯一带 tkinter 的解释器是系统 Store 版 **Python 3.7.9**（tk 8.6），启动用别名：
  `%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe`。真实路径在 `C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.7_*` 下，受 ACL 保护，**不要直接引用**。
- 结论：tkinter GUI 工具（如 `循环按键脚本/`）用该别名 pythonw 启动，代码必须**兼容 Python 3.7**（不用 walrus、不用运行期 `X | Y` 注解、不用 `Path.unlink(missing_ok=)`、不用 `threading.excepthook`；用 `from __future__ import annotations` 让类型注解变字符串）。
- tkinter 导入用 try/except 包住，缺 tkinter 时仍能跑纯逻辑的 `--selftest`。
- ⚠️ 现存问题：`自动脚本开发/启动.bat`、`脚本开发工具/地图标记工具/地图标记工具.bat`、`脚本开发工具/YOLO自动标注工具/自动标注工具.bat` 都指向 `envs\yolo\Scripts\pythonw.exe`，实测 `地图标记工具/main.py` 报 `ModuleNotFoundError: No module named 'tkinter'`（pythonw 无控制台 → 静默退出）。如需修复，改成上面的别名 pythonw。

## 截图与进程清理（自建 GUI 工具时）
- 抓自己启动的窗口图用 PrintWindow（`GetClientRect` + `PrintWindow(hwnd, dc, 1)` + `GetDIBits`），不会挡住用户其他窗口；不要用全屏 ImageGrab 截图当交付物。
- GetDIBits 出来的 32bpp 是 BGRA，转 RGB 必须写成 `arr[..., :3][..., ::-1]`（**三个索引**），少写一位会把图片左右镜像。
- 同一进程里除主窗口外还有 Tk 的 `Mode Indicator` 辅助窗口（约 34×34），按 PID 找窗口时要按标题（`循环按键脚本`）挑，不要取第一个。
- 清理自己启动的进程**必须用 `subprocess.Popen` 拿到的 PID**（`OpenProcess`+`TerminateProcess`），**禁止按窗口标题模糊匹配**：曾因标题含目录名而误杀用户打开的 CodeBuddy 编辑器窗口。

## 热键启停类 GUI 工具（循环按键脚本）
- 热键轮询 `GetAsyncKeyState(vk) & 0x8000` + `prev` 记边沿：一次物理按键 = 一次切换（实测 `keybd_event` 注入一次 F12 只切换一次），逻辑本身不会自触发（脚本只注入 Ctrl/方向/空格/数字，与 F8/F9/F12 不重叠）。
- 「按了开始马上又停止」的根因基本是**同一个热键被按了两次**（F12 是切换键，第二次即停止）。确认方式：看 `run.log` 里 `热键 F12 按下` 的行数；也可用注入探针复现（见下）。
- 切换型热键必须做到：① 每次按下都写日志（含来源，如 `已停止（F12）：本次运行 5 秒`）；② 0.3 秒内的重复触发忽略并记日志；③ 「开场缓冲」这类静默等待在状态栏显示剩余秒数。否则用户会以为没生效而再按一次把自己关掉。
- 用命名互斥体做单实例保护：`CreateMutexW(None, 1, NAME)` 后判 `GetLastError()==183`(ERROR_ALREADY_EXISTS)，第二个实例把已有窗口 `ShowWindow(hwnd, 9)`+`SetForegroundWindow` 提前台再退出。多实例会抢同一个全局热键与前台按键。
- 注入式热键探针（验证/复现用，见 `循环按键脚本/_probe_f12.py`）：`Popen` 起 pythonw 应用 → 按 PID 找主窗口 → `SetForegroundWindow` → `keybd_event(vk, 0, 0, 0)` 按下、`keybd_event(vk, 0, 2, 0)` 抬起 → 读 `run.log` 增量断言。**注入前务必先把焦点切到自己的窗口**，否则按键会打进用户的 IDE/游戏。
- 按键探针（量「脚本实际发了什么」用，见 `循环按键脚本/_probe_keys.py`）：`timeBeginPeriod(1)` + 1ms 轮询 `GetAsyncKeyState(VK_CTRL/VK_SPACE/VK_LEFT/VK_RIGHT) & 0x8000`，记录按下/松开沿与间隔，再按「方向键按下」切分统计每轮 Ctrl 次数。比读代码可靠。

## 按键类脚本与目标程序的交互（重要经验）
- **「目标程序只响应 1 次」≠「脚本只发了 1 次」**：先量（按键探针 / 自身日志），别改代码。本工具里脚本每轮发 Ctrl×3（每次按住 0.1s、间隔 0.1s）并经 1ms 轮询证实，但游戏里只表现为 1 次动作。
- 原因是目标程序有**动作动画/冷却锁**：间隔 0.1s 的连按会全部落在同一次动作里被丢弃。**要让 N 次连按都生效，间隔必须大于目标程序一次动作的时长**（用「手速极限」估：手动最短多久能出第 2 次动作，间隔取略大于它）。
- 因此连按类间隔参数（`ctrl_gap` 等）默认不要给太小，并且要在日志里能看见「一轮实际发了什么」，比如 `第 N 轮：Ctrl×3（按住 0.1 / 间隔 0.5 秒） → 右方向+空格`（节流 5 秒一条，每阶段首轮必记）。

## 世界模型格式
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

## system_setting.json（按窗口名全局配置）
文件: `脚本开发工具/地图标记工具/marker_output/system_setting.json`
结构: `{窗口名: {template_rect, window_size, dot_hsv_lower, dot_hsv_upper}}`
- `template_rect`: 角色名区域 [x1,y1,x2,y2]
- `window_size`: 游戏窗口 [w, h]
- `dot_hsv_lower` / `dot_hsv_upper`: 小地图黄点 HSV 检测阈值（不同游戏版本颜色饱和度差异很大，需逐游戏调）
- 同步至 `自动脚本开发/maps/system_setting.json`

## 自动脚本
- 启动时通过 `_detect_start_waypoint()` 自动匹配角色最近途经点，跳过已走过的。
- 固定路线决策优先级：绳梯 → 清怪（身前优先，身后转身）→ 跨平台 → 到达途经点 → 步行。
- 过渡命令（爬梯/跳跃/闪现）创建后设置 `transition.in_progress = True`，通过 `TransitionController` 管理生命周期。
- `ClimbCommand` 有完整的绳梯卡住检测（最近 10 帧位置不变 + |dx| < 2px → 恢复 0.5s）。
- 技能系统通过 `_get_effective_skill(mc)` 根据释放规则和怪物数返回技能 info dict。
- 调试截图默认打开，勾选后每 tick 保存到 `debug_frames/`，`frame_00~19.png` 循环覆盖。
