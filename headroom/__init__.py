"""Headroom — 通用上下文压缩层。

多种专用压缩器(日志/JSON/代码/文本)经 ContentRouter 自动路由,
压缩结果附带 CCR 标记,LLM 可随时取回原文(按需查阅,而非单向摘要)。
"""

__version__ = "0.1.0"
