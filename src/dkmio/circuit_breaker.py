"""Circuit breaker for DynamoDB resilience.

Implements the CLOSED → OPEN → HALF_OPEN state machine to protect
applications from cascading failures when DynamoDB is unavailable or
severely throttled.

States:
    CLOSED   — Normal operation. Failures are counted.
    OPEN     — All calls are rejected immediately with :class:`CircuitOpenError`.
               No DynamoDB calls are made.
    HALF_OPEN — One probe request is allowed through to test recovery.
               Success → CLOSED. Failure → back to OPEN.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class CircuitBreakerConfig:
    """Configuration for the circuit breaker.

    Args:
        failure_threshold: Number of consecutive infrastructure failures
            before the circuit opens. Client errors (bad keys, condition
            failures) do not count. Defaults to ``5``.
        recovery_timeout: Seconds to wait in OPEN state before allowing
            a single probe request through (HALF_OPEN). Defaults to ``30``.

    Example::

        from dkmio import DynamoDB, CircuitBreakerConfig

        db = DynamoDB(
            region_name="us-east-1",
            circuit_breaker=CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=60,
            ),
        )
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0


class CircuitBreaker:
    """Thread-safe circuit breaker implementation.

    Not instantiated directly — created internally by :class:`~dkmio.client.DynamoDB`
    when a :class:`CircuitBreakerConfig` is provided.

    Access the current state via :attr:`state` and reset manually with
    :meth:`reset` (useful for health checks or admin tooling).
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Current state: ``"closed"``, ``"open"``, or ``"half_open"``."""
        return self._state

    def execute(self, fn: Callable[..., T]) -> T:
        """Execute *fn* through the circuit breaker.

        Args:
            fn: Zero-argument callable that performs a DynamoDB operation
                and raises :class:`~dkmio.exceptions.DkmioError` on failure.

        Returns:
            The return value of *fn*.

        Raises:
            CircuitOpenError: If the circuit is OPEN or a HALF_OPEN probe
                is already in progress.
            Any exception raised by *fn*.
        """
        from .exceptions import CircuitOpenError

        with self._lock:
            if self._state == self.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0)
                if elapsed >= self._config.recovery_timeout:
                    self._state = self.HALF_OPEN
                    captured_state = self.HALF_OPEN
                else:
                    remaining = self._config.recovery_timeout - elapsed
                    raise CircuitOpenError(
                        f"Circuit breaker is OPEN. "
                        f"DynamoDB calls rejected for {remaining:.1f}s more."
                    )
            elif self._state == self.HALF_OPEN:
                raise CircuitOpenError(
                    "Circuit breaker is HALF_OPEN. Probe request in progress."
                )
            else:
                captured_state = self.CLOSED

        try:
            result = fn()
        except Exception as exc:
            if self._is_infra_error(exc):
                with self._lock:
                    if captured_state == self.HALF_OPEN:
                        self._state = self.OPEN
                        self._opened_at = time.monotonic()
                    else:
                        self._failure_count += 1
                        if self._failure_count >= self._config.failure_threshold:
                            self._state = self.OPEN
                            self._opened_at = time.monotonic()
            raise

        with self._lock:
            if captured_state == self.HALF_OPEN:
                self._state = self.CLOSED
                self._failure_count = 0
            elif self._state == self.CLOSED:
                self._failure_count = 0

        return result

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state.

        Useful for health-check endpoints, admin tooling, or test setup.
        """
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None

    def _is_infra_error(self, exc: BaseException) -> bool:
        """Return True if *exc* represents an infrastructure failure.

        Client errors (bad arguments, condition failures, missing keys) do
        not count against the circuit. Only throttling, outages, and
        unclassified AWS errors trip the breaker.
        """
        from .exceptions import (
            CircuitOpenError,
            CollectionSizeError,
            ConditionError,
            InvalidProjectionError,
            MissingKeyError,
            TableNotFoundError,
            TransactionError,
            ValidationError,
        )

        non_infra = (
            ConditionError,
            MissingKeyError,
            ValidationError,
            InvalidProjectionError,
            TableNotFoundError,
            CollectionSizeError,
            TransactionError,
            CircuitOpenError,
        )
        return not isinstance(exc, non_infra)
