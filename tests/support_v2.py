"""Deterministic controls for disposable v2 test stores and workers."""

from math import isfinite
from multiprocessing.connection import Connection


class FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0 or not isfinite(self._now + seconds):
            raise ValueError('advance requires a finite nonnegative interval')
        self._now += seconds


class InjectedFault(RuntimeError):
    pass


class FaultInjector:
    def __init__(self, *points: str) -> None:
        self.points = frozenset(points)

    def hit(self, point: str) -> None:
        if point in self.points:
            raise InjectedFault(point)


def subprocess_barrier(pipe: Connection, point: str, timeout: float = 10) -> None:
    """Child announces a named checkpoint; parent echoes it to release or kills child."""
    pipe.send(point)
    if not pipe.poll(timeout):
        raise TimeoutError(point)
    if pipe.recv() != point:
        raise ValueError('unexpected barrier release')
