"""gpu_utils.py — GPU 自动检测与设备管理

支持三种 GPU 后端：
  - NVIDIA CUDA (torch.cuda)
  - AMD/Intel DirectML (onnxruntime-directml)
  - CPU 回退

主要功能：
  - detect_gpu(): 检测 GPU 类型和名称
  - get_onnx_providers(): 返回 ONNX Runtime provider 列表（DirectML 优先）
  - patch_onnx_for_gpu(): monkey-patch onnxruntime 使其自动使用 DirectML
  - get_device_list(): 返回 UI 下拉框可选的设备列表
  - resolve_device(): 将用户选择的设备名解析为 ultralytics 可用的 device 字符串
"""

import ctypes
from pathlib import Path


def _detect_nvidia() -> bool:
    """检测是否存在 NVIDIA GPU（通过 nvcuda.dll）。"""
    try:
        ctypes.WinDLL("nvcuda.dll")
        return True
    except OSError:
        return False


def _detect_directml() -> bool:
    """检测 DirectML 是否可用（通过 onnxruntime-directml）。"""
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except ImportError:
        return False


def _get_nvidia_gpu_name() -> str | None:
    """通过 CUDA API 获取 NVIDIA GPU 名称。"""
    try:
        nvcuda = ctypes.WinDLL("nvcuda.dll")
        if nvcuda.cuInit(0) != 0:
            return None
        count = ctypes.c_int(0)
        if nvcuda.cuDeviceGetCount(ctypes.byref(count)) != 0:
            return None
        if count.value == 0:
            return None
        name = ctypes.create_string_buffer(256)
        if nvcuda.cuDeviceGetName(name, 256, 0) == 0:
            return name.value.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return None


def _get_directml_device_name() -> str | None:
    """获取 DirectML 设备名称。"""
    try:
        import onnxruntime as ort
        # 尝试创建一个临时 session 来枚举 DirectML 设备
        # onnxruntime 不直接暴露设备名，用一个小的 dummy 模型来测试
        # 这里用环境检测替代
        if _detect_amd_gpu():
            return "AMD GPU (DirectML)"
        return "GPU (DirectML)"
    except Exception:
        return None


def _detect_amd_gpu() -> bool:
    """检测是否存在 AMD GPU（通过 atidxx64.dll）。"""
    try:
        ctypes.WinDLL("atidxx64.dll")
        return True
    except OSError:
        return False


def detect_gpu() -> dict:
    """检测 GPU 类型和名称。

    返回:
        {"type": "nvidia"|"directml"|"none", "name": str|None, "backend": str}
    """
    if _detect_nvidia():
        name = _get_nvidia_gpu_name()
        return {
            "type": "nvidia",
            "name": name or "NVIDIA GPU",
            "backend": "CUDA",
        }

    if _detect_directml():
        name = _get_directml_device_name()
        return {
            "type": "directml",
            "name": name or "DirectML GPU",
            "backend": "DirectML",
        }

    return {
        "type": "none",
        "name": None,
        "backend": "CPU",
    }


def get_onnx_providers() -> list[str | tuple]:
    """返回 ONNX Runtime provider 列表，GPU 优先。

    NVIDIA CUDA → CUDAExecutionProvider 优先
    AMD/其他 → DmlExecutionProvider 优先
    无 GPU → CPUExecutionProvider
    """
    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
    except ImportError:
        return ["CPUExecutionProvider"]

    # NVIDIA CUDA 优先
    if "CUDAExecutionProvider" in available and _detect_nvidia():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]

    # DirectML 次之（AMD/Intel GPU）
    if "DmlExecutionProvider" in available:
        return ["DmlExecutionProvider", "CPUExecutionProvider"]

    return ["CPUExecutionProvider"]


def patch_onnx_for_gpu() -> bool:
    """Monkey-patch onnxruntime.InferenceSession 使其自动使用 DirectML。

    当 ultralytics 加载 ONNX 模型时，它只检查 CUDA/CoreML provider，
    不检查 DirectML。此函数在 session 创建时自动注入 DirectML provider。

    Returns:
        True 表示 DirectML patch 已生效，False 表示无需 patch（CUDA 可用或无 DirectML）
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return False

    available = ort.get_available_providers()
    if "DmlExecutionProvider" not in available:
        return False

    # 如果有 NVIDIA CUDA，不需要 patch（CUDA 性能更好）
    if "CUDAExecutionProvider" in available and _detect_nvidia():
        return False

    # 检查是否已经 patch 过
    if getattr(ort.InferenceSession, "_directml_patched", False):
        return True

    _original_init = ort.InferenceSession.__init__

    def _patched_init(self, *args, **kwargs):
        providers = kwargs.get("providers")
        if providers is None:
            # 未指定 provider → DirectML 优先
            kwargs["providers"] = ["DmlExecutionProvider", "CPUExecutionProvider"]
        elif isinstance(providers, list):
            # 已指定 provider 列表 → 检查是否需要注入 DirectML
            provider_names = [
                p[0] if isinstance(p, (tuple, list)) else p for p in providers
            ]
            has_cuda = any("CUDA" in p for p in provider_names)
            has_dml = "DmlExecutionProvider" in provider_names
            if not has_cuda and not has_dml:
                # 只有 CPU → 在前面加 DirectML
                kwargs["providers"] = ["DmlExecutionProvider"] + providers
        _original_init(self, *args, **kwargs)

    _patched_init._directml_patched = True
    ort.InferenceSession.__init__ = _patched_init
    return True


def get_device_list() -> list[tuple[str, str]]:
    """返回 UI 下拉框可选的设备列表。

    返回: [(value, display_text), ...]
    """
    gpu = detect_gpu()
    devices = [("auto", "自动检测")]

    if gpu["type"] == "nvidia":
        devices.append(("cuda:0", f"GPU 0 ({gpu['name']})"))
    elif gpu["type"] == "directml":
        devices.append(("dml:0", f"GPU ({gpu['name']})"))

    devices.append(("cpu", "CPU"))
    return devices


def resolve_device(device_str: str) -> str:
    """将用户选择的设备名解析为 ultralytics 可用的 device 字符串。

    "auto" → 根据 GPU 类型自动选择
    "cuda:0" / "dml:0" → 原样返回
    "cpu" → "cpu"
    """
    if device_str == "auto":
        gpu = detect_gpu()
        if gpu["type"] == "nvidia":
            return "cuda:0"
        elif gpu["type"] == "directml":
            # DirectML 通过 ONNX provider 实现，ultralytics device 设为 cpu
            # 实际推理由 patch_onnx_for_gpu() 注入 DirectML provider
            return "cpu"
        return "cpu"

    if device_str.startswith("dml:"):
        # DirectML 通过 ONNX provider 实现，ultralytics device 设为 cpu
        return "cpu"

    return device_str


def get_gpu_status_text() -> str:
    """返回 GPU 状态文本（用于 UI 显示）。"""
    gpu = detect_gpu()
    if gpu["type"] == "nvidia":
        return f"GPU: {gpu['name']} (CUDA)"
    elif gpu["type"] == "directml":
        dml_patched = patch_onnx_for_gpu()
        patch_status = "已启用" if dml_patched else "未启用"
        return f"GPU: {gpu['name']} (DirectML, {patch_status})"
    return "GPU: 未检测到 (使用 CPU)"


def is_gpu_available() -> bool:
    """是否有可用的 GPU。"""
    return detect_gpu()["type"] != "none"


def should_use_onnx(device_str: str) -> bool:
    """判断是否应该使用 ONNX 模型进行推理（而非 PyTorch .pt）。

    当设备为 DirectML 时，必须使用 ONNX 格式（PyTorch 不支持 DirectML）。
    用户显式选择 CPU 或 CUDA 时不使用 ONNX（直接用 PyTorch .pt/.engine）。
    """
    # 用户显式选 CPU → 不用 ONNX
    if device_str == "cpu":
        return False
    # 用户显式选 CUDA → 不用 ONNX（PyTorch CUDA 直接处理）
    if device_str.startswith("cuda"):
        return False

    # DirectML 设备 → 必须使用 ONNX
    if device_str.startswith("dml:"):
        return True

    gpu = detect_gpu()
    if gpu["type"] == "directml":
        return True

    return False
