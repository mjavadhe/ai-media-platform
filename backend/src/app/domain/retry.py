"""Retry policy: exponential backoff with jitter and a bounded attempt budget.

Contract section 12. Two independent bounds keep a retry storm impossible:

* the *failure class* decides whether a retry is allowed at all
  (:func:`app.domain.enums.is_retryable`), and
* ``max_retries`` caps the number of retries regardless of class, so even an
  unknown/retryable error stops after the budget.

Jitter is full-jitter (``random.uniform(0, min(cap, base * 2**n))``) so a burst of
simultaneous OOM failures does not resynchronise into another burst.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.domain.enums import FailureClass, classify_failure, is_retryable


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Outcome of the retry policy for one failure."""

    should_retry: bool
    failure_class: FailureClass
    attempt_no: int
    retry_count: int
    max_retries: int
    delay_seconds: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "should_retry": self.should_retry,
            "failure_class": self.failure_class.value,
            "attempt_no": self.attempt_no,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "delay_seconds": round(self.delay_seconds, 3),
            "reason": self.reason,
        }


def compute_backoff(
    retry_count: int,
    *,
    base_seconds: float = 2.0,
    max_seconds: float = 300.0,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """Full-jitter exponential backoff for ``retry_count`` (1-based)."""
    if retry_count <= 0:
        return 0.0
    ceiling = min(max_seconds, base_seconds * (2 ** (retry_count - 1)))
    if not jitter:
        return ceiling
    generator = rng or random
    return generator.uniform(0.0, ceiling)


def decide_retry(
    failure: str | FailureClass,
    *,
    attempt_no: int,
    max_retries: int = 3,
    base_seconds: float = 2.0,
    max_seconds: float = 300.0,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> RetryDecision:
    """Decide whether ``attempt_no`` should be retried and after how long.

    ``retry_count`` is derived as ``attempt_no - 1`` because attempt numbering is
    1-based: after attempt 1 fails, one retry has been consumed.
    """
    failure_class = classify_failure(failure)
    retry_count = max(0, attempt_no - 1)

    if failure_class is FailureClass.CANCELLED:
        return RetryDecision(
            False, failure_class, attempt_no, retry_count, max_retries, reason="job cancelled"
        )
    if not is_retryable(failure_class):
        return RetryDecision(
            False,
            failure_class,
            attempt_no,
            retry_count,
            max_retries,
            reason=f"{failure_class.value} is not retryable",
        )
    if retry_count >= max_retries:
        return RetryDecision(
            False,
            failure_class,
            attempt_no,
            retry_count,
            max_retries,
            reason=f"retry budget exhausted ({max_retries} retries)",
        )
    delay = compute_backoff(
        retry_count + 1,
        base_seconds=base_seconds,
        max_seconds=max_seconds,
        jitter=jitter,
        rng=rng,
    )
    return RetryDecision(
        True,
        failure_class,
        attempt_no,
        retry_count,
        max_retries,
        delay_seconds=delay,
        reason=f"retryable {failure_class.value}",
    )


__all__ = ["RetryDecision", "compute_backoff", "decide_retry"]
