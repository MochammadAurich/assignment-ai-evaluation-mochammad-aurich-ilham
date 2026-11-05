"""
Pencatatan metrik per-request untuk keperluan evaluasi (Assignment 5):
latency per tahap dan penggunaan token LLM.

Modul ini hanya MENGUKUR, tidak mengubah perilaku sistem. Hasilnya dikirim
ke client lewat header HTTP `X-Metrics` pada endpoint /ask.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar = ContextVar("request_metrics", default=None)


def start() -> dict:
    """Mulai pencatatan untuk satu request."""
    metrics = {
        "timings_ms": {},
        "tokens": {"prompt": 0, "output": 0, "thinking": 0, "total": 0},
        "llm_calls": 0,
        "intent_source": None,
    }
    _current.set(metrics)
    return metrics


@contextmanager
def timed(stage: str):
    """Ukur durasi sebuah tahap (milidetik). Aman dipakai juga di dalam thread (asyncio.to_thread)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        metrics = _current.get()
        if metrics is not None:
            elapsed = (time.perf_counter() - t0) * 1000
            previous = metrics["timings_ms"].get(stage, 0.0)
            metrics["timings_ms"][stage] = round(previous + elapsed, 1)


def add_usage(response) -> None:
    """Catat jumlah token dari satu panggilan LLM (usage_metadata dari respons Gemini)."""
    metrics = _current.get()
    if metrics is None:
        return
    metrics["llm_calls"] += 1
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return
    metrics["tokens"]["prompt"] += getattr(usage, "prompt_token_count", 0) or 0
    metrics["tokens"]["output"] += getattr(usage, "candidates_token_count", 0) or 0
    metrics["tokens"]["thinking"] += getattr(usage, "thoughts_token_count", 0) or 0
    metrics["tokens"]["total"] += getattr(usage, "total_token_count", 0) or 0


def set_field(key: str, value) -> None:
    """Catat informasi tambahan, misalnya sumber intent (regex / llm / keyword_fallback)."""
    metrics = _current.get()
    if metrics is not None:
        metrics[key] = value