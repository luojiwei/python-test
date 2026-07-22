"""edge_types.py — 世界模型边类型常量。

消除 "rope"/"jump"/"flash" 字符串硬编码，提供统一常量 + 中文翻译。
"""


class EdgeType:
    """世界模型中平台间连接边的类型。"""
    ROPE: str = "rope"    # 绳梯
    JUMP: str = "jump"    # 跳跃
    FLASH: str = "flash"  # 闪现/瞬移

    # 所有有效边类型的集合
    ALL: tuple[str, ...] = (ROPE, JUMP, FLASH)


# 中文翻译
TYPE_CN: dict[str, str] = {
    EdgeType.ROPE: "绳梯",
    EdgeType.JUMP: "跳跃",
    EdgeType.FLASH: "闪现",
}
