"""重试与熔断：把"偶发故障"与"系统故障"区分开。"""

from __future__ import annotations

import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def retry(
    times: int = 3,
    *,
    base_delay: float = 0.8,
    max_delay: float = 8.0,
    jitter: float = 0.2,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    logger: logging.Logger | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """指数退避重试（带抖动），只重试指定异常。"""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_error: BaseException | None = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last_error = exc
                    if attempt >= times:
                        break
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, jitter * delay)
                    if logger:
                        logger.warning(
                            "%s 第 %d/%d 次失败：%s，%.1fs 后重试",
                            func.__name__,
                            attempt,
                            times,
                            exc,
                            delay,
                        )
                    time.sleep(delay)
            raise last_error if last_error else RuntimeError("retry failed")

        return wrapper

    return decorator


@dataclass
class CircuitBreaker:
    """连续失败 N 次后熔断，避免整批任务被单个坏依赖拖死。"""

    failure_threshold: int = 3
    reset_timeout: float = 60.0
    failures: int = 0
    opened_at: float | None = None
    _logger: logging.Logger | None = field(default=None, repr=False)

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at >= self.reset_timeout:
            # 半开：允许再试一次
            self.opened_at = None
            self.failures = 0
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold and self.opened_at is None:
            self.opened_at = time.time()
            if self._logger:
                self._logger.warning("熔断器打开，%.0fs 内不再调用该依赖", self.reset_timeout)

    def allow(self) -> bool:
        return not self.is_open


__all__ = ["CircuitBreaker", "retry"]
