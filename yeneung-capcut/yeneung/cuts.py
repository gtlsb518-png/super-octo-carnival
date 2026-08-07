"""무음 구간 자동 컷.

오디오 에너지를 재서 조용한 구간을 찾고, 남길 구간(keep range) 목록을 만든다.
컷을 하면 뒤쪽 시간이 전부 당겨지므로, 원본 시간 → 편집 후 시간으로
바꿔주는 TimelineMap 도 함께 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import CutConfig

_HOP = 0.02  # 20ms 단위로 음량 측정
_EPS = 1e-10


@dataclass(frozen=True)
class KeepRange:
    """원본 영상에서 남길 구간 (초)."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class TimelineMap:
    """원본 시각 → 컷 편집 후 시각 변환기."""

    def __init__(self, keeps: list[KeepRange]) -> None:
        self._keeps = keeps
        # 각 구간이 편집본에서 시작하는 시각
        self._offsets: list[float] = []
        acc = 0.0
        for k in keeps:
            self._offsets.append(acc)
            acc += k.duration
        self.total = acc

    @property
    def keeps(self) -> list[KeepRange]:
        return self._keeps

    def to_cut(self, t: float) -> float:
        """원본 시각 t 를 편집본 시각으로. 잘려나간 구간이면 가장 가까운 경계로."""
        if not self._keeps:
            return 0.0
        for keep, off in zip(self._keeps, self._offsets):
            if t < keep.start:
                return off                      # 잘려나간 앞쪽 → 구간 시작으로
            if t <= keep.end:
                return off + (t - keep.start)
        return self.total

    def segment_of(self, t: float) -> tuple[int, float] | None:
        """편집본 시각 t 가 몇 번째 구간의 몇 초 지점인지. 범위 밖이면 None."""
        for i, (keep, off) in enumerate(zip(self._keeps, self._offsets)):
            if off <= t < off + keep.duration:
                return i, t - off
        if self._keeps and abs(t - self.total) < 1e-6:
            last = len(self._keeps) - 1
            return last, self._keeps[last].duration
        return None

    def cut_start_of(self, index: int) -> float:
        """index 번째 구간이 편집본에서 시작하는 시각."""
        return self._offsets[index]


def _frame_db(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """20ms 프레임별 RMS 음량(dBFS)."""
    hop = max(1, int(sample_rate * _HOP))
    n = len(samples) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return (20.0 * np.log10(rms + _EPS)).astype(np.float32)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """True 가 연속된 구간들을 (시작, 끝exclusive) 인덱스로."""
    if mask.size == 0:
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))


def detect_keep_ranges(
    samples: np.ndarray,
    sample_rate: int,
    duration: float,
    cfg: CutConfig,
) -> list[KeepRange]:
    """남길 구간을 계산한다.

    오디오가 없거나 컷이 꺼져 있으면 영상 전체를 하나의 구간으로 돌려준다.
    """
    whole = [KeepRange(0.0, duration)]
    if not cfg.enabled or samples.size == 0:
        return whole

    db = _frame_db(samples, sample_rate)
    if db.size == 0:
        return whole

    silent = db < cfg.threshold_db
    min_frames = max(1, int(round(cfg.min_silence / _HOP)))

    # 충분히 긴 무음 구간만 '잘라낼 구간'으로 채택
    drops: list[tuple[float, float]] = []
    for lo, hi in _runs(silent):
        if hi - lo < min_frames:
            continue
        start = lo * _HOP + cfg.padding
        end = hi * _HOP - cfg.padding
        if end > start:
            drops.append((start, end))

    if not drops:
        return whole

    # 잘라낼 구간의 여집합 = 남길 구간
    keeps: list[KeepRange] = []
    cursor = 0.0
    for start, end in drops:
        if start > cursor:
            keeps.append(KeepRange(cursor, min(start, duration)))
        cursor = max(cursor, end)
    if cursor < duration:
        keeps.append(KeepRange(cursor, duration))

    if not cfg.trim_edges:
        # 앞뒤 무음을 살려 원본 전체 길이를 유지
        if keeps:
            keeps[0] = KeepRange(0.0, keeps[0].end)
            keeps[-1] = KeepRange(keeps[-1].start, duration)

    # 전부 무음이었다면 컷하지 않고 원본을 그대로 쓴다
    return _merge_short(keeps, cfg.min_keep) or whole


def _merge_short(keeps: list[KeepRange], min_keep: float) -> list[KeepRange]:
    """너무 짧은 조각은 앞 조각에 붙여 깜빡임을 막는다."""
    out: list[KeepRange] = []
    for k in keeps:
        if k.duration <= 0:
            continue
        if k.duration < min_keep and out:
            out[-1] = KeepRange(out[-1].start, k.end)
        else:
            out.append(k)
    # 첫 조각이 여전히 짧으면 뒤 조각에 흡수
    if len(out) > 1 and out[0].duration < min_keep:
        out[1] = KeepRange(out[0].start, out[1].end)
        out.pop(0)
    return out or keeps
