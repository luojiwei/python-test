@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   自动脚本 - 依赖安装
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.9+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [信息] Python 版本:
python --version
echo.

:: 检查 pip
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] pip 不可用，请先安装 pip
    pause
    exit /b 1
)

echo [步骤 1/3] 升级 pip...
python -m pip install --upgrade pip

echo.
echo [步骤 2/3] 检测 GPU 并安装 PyTorch...

:: 检测 NVIDIA 显卡
set "GPU_TYPE=none"
nvidia-smi >nul 2>&1
if not errorlevel 1 (
    set "GPU_TYPE=nvidia"
    for /f "tokens=*" %%i in ('nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul') do (
        echo [信息] 检测到 GPU: %%i
    )
    for /f "tokens=9" %%i in ('nvidia-smi ^| findstr "CUDA Version"') do (
        echo [信息] CUDA 版本: %%i
    )
    echo [安装] PyTorch (CUDA 版)...
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
) else (
    :: 尝试检测 AMD/Intel 显卡
    wmic path win32_videocontroller get name 2>nul | findstr /i "AMD Radeon Intel Arc Iris" >nul 2>&1
    if not errorlevel 1 (
        set "GPU_TYPE=amd_intel"
        for /f "tokens=*" %%i in ('wmic path win32_videocontroller get name 2^>nul ^| findstr /v "^$" ^| findstr /v /i "Microsoft"') do (
            echo [信息] 检测到显卡: %%i
        )
        echo [安装] PyTorch (CPU 版) + DirectML (AMD/Intel GPU 加速)...
        python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
        python -m pip install torch-directml
    ) else (
        echo [信息] 未检测到独立显卡
        echo [安装] PyTorch (CPU 版)...
        python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    )
)

if errorlevel 1 (
    echo [警告] PyTorch 安装失败，尝试 CPU 版本...
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
)

echo.
echo [步骤 3/3] 安装其余依赖...
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [警告] 部分依赖安装失败，请检查上方错误信息。
    echo.
    echo 常见问题:
    echo   1. 如果 ultralytics 安装失败，手动执行:
    echo      pip install ultralytics
    echo.
    echo   2. 如果 opencv-python 失败，尝试:
    echo      pip install opencv-python-headless
    pause
    exit /b 1
)

:: 验证 GPU 是否可用
echo.
if "%GPU_TYPE%"=="nvidia" (
    echo [验证] 检查 CUDA 是否可用...
    python -c "import torch; print('   CUDA 可用:', torch.cuda.is_available()); print('   GPU 名称:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
) else if "%GPU_TYPE%"=="amd_intel" (
    echo [验证] 检查 DirectML 是否可用...
    python -c "import torch_directml; d=torch_directml.device(); print('   DirectML 设备:', d)"
) else (
    echo [信息] 使用 CPU 模式运行
)

echo.
echo ========================================
echo   安装完成！现在可以运行启动.bat了
echo ========================================
pause
