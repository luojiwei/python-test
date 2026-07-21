# 项目规则 — 工具生成通用要求

> 以下规则适用于在 `脚本开发工具/` 下创建的所有工具。
> 每次生成新工具时自动遵循，无需用户重复说明。

---

## 1. 文件夹独立性

- 每个工具在独立子文件夹中，所有输入输出文件夹都在该子文件夹内
- 对外依赖通过 `Path(__file__).resolve().parent` (Python) 或 `%~dp0` (批处理) 自定位，不写死绝对路径
- 如需引用兄弟工具的输出，统一写到脚本内并注明依赖关系

## 2. 启动器规范

- **只使用 `.bat` 启动器**，其他形式均不可用：
  - `.pyw` — 管理版 Python 未注册文件关联，双击无反应
  - `.vbs` — WScript.Shell 在安全策略下被阻止
  - `.lnk` 快捷方式 — 需要通过 COM 创建，也被阻止
- **`.bat` 模板**（两条路，按情况选）：

  **方式 A — `cd /d` + 相对路径**（推荐，绝对中文路径不经过 `start` 传递）：
  ```bat
  @echo off
  cd /d "D:\Program Files (x86)\Tencent\WorkBuddy\脚本开发工具\工具文件夹"
  start "" "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe" "主脚本.py"
  ```

  **方式 B — `%~dp0` 自动展开**（工具文件夹名不含括号时可用）：
  ```bat
  @echo off
  start "" "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe" "%~dp0主脚本.py"
  ```
- `start ""` 使 cmd 窗口即时关闭，pythonw.exe 在后台运行 GUI
- `.bat` 内不放依赖检查、目录创建等逻辑，全部放 Python 入口处

## 3. .bat 文件写入规范（PowerShell）

- **必须用 `Default` 编码**（中文 Windows 上 = GBK），不能用 ASCII/UTF-8：
  ```powershell
  [System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::Default)
  ```
- 用 ASCII 编码 → 中文字符变 `?` → 路径无效
- 用 UTF-8 无 BOM → cmd 按 GBK 解析 → 乱码

## 4. 依赖自检

- Python 脚本入口处自动检查第三方库（mss, Pillow, cv2, numpy 等）
- 缺失时静默调用 `pip install`（不弹窗，不阻塞）
- 安装失败则写入日志文件到桌面，并弹出错误提示

## 5. 崩溃日志

- 启动时写 `桌面/toolname_startup.log`
- 捕获全局异常写 `桌面/toolname_crash.log` 并弹 MessageBox

## 6. 编码与路径

- `.bat` 文件：**纯 ASCII 注释**，不使用中文（避免 GBK/UTF-8 乱码）
- `.py` 文件：UTF-8 编码
- 路径含 `(x86)` 等括号且需要在 `if` 块中引用时：用 `setlocal enabledelayedexpansion` + `!VAR!`
- 路径含空格时：始终用双引号包裹
- 避免在 `start` 命令参数中传递含中文的绝对路径，用 `cd /d` + 相对路径代替

## 7. 环境路径

- 默认 Python: `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe`
- YOLO Python: `C:\Users\Administrator\.workbuddy\binaries\python\envs\yolo\Scripts\pythonw.exe`
- 需要 cv2/numpy 等重型库时用 yolo 环境，基础工具用 default 环境

## 8. UI 风格

- tkinter GUI，字体 `Microsoft YaHei`
- Notebook 标签页风格
- 按钮颜色：开始/运行=#4ecdc4，停止=#ff6b6b，危险操作(删除/重置)=#e74c3c
- 训练日志用暗色终端风格 (#1e1e1e 背景, #d4d4d4 文字)

## 9. 桌面禁止保存

- **所有输出文件一律保存在工具自身文件夹中**，禁止写入桌面（~/Desktop）
- 包括但不限于：截图、标记图、日志、临时文件
- 例外：崩溃日志（启动失败时用户看不到工具文件夹，桌面是唯一可靠位置）

## 10. 我优先验证

- **凡是能通过截图/抓帧/模拟数据直接验证的，必须先验证再问用户**
- 不要反复让用户"跑一次看看" — 先用自己的代码调通
- 需要用户信息时才问（如"你现在的窗口标题是什么"）

## 11. 游戏窗口自动恢复/还原

- 截图或检测前：检查游戏窗口是否最小化（`IsIconic`），是则调用 `ShowWindow(hwnd, 9)` 恢复
- **截完图立即最小化**：调用 `ShowWindow(hwnd, 6)`，不管之前什么状态
- 不干扰用户的窗口管理习惯
