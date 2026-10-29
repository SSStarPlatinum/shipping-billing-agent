"""Shipping Billing Agent.

多格式文档解析的航运智能账单助手智能体（AI AGENT）。

对外主要入口：
    from shipping_billing import BillingAgent, Settings
    result = BillingAgent(Settings()).run([...paths...])
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__", "BillingAgent", "Settings"]


def __getattr__(name: str):  # 延迟导入，避免导入包时就拉起重依赖
    if name == "BillingAgent":
        from .pipeline import BillingAgent

        return BillingAgent
    if name == "Settings":
        from .config import Settings

        return Settings
    raise AttributeError(name)
