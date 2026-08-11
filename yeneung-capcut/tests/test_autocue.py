"""규칙 기반 큐시트 테스트 (API 안 쓰는 모드).

맥락을 이해하지 못하는 대신, 글자로 알 수 있는 신호는 놓치지 않아야 한다.
그리고 결과가 예능처럼 보이려면 스타일이 한쪽으로 쏠리면 안 된다.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung import autocue  # noqa: E402
from yeneung.transcribe import Utterance  # noqa: E402

_SFX = ["ding", "drum", "boing", "crickets", "correct", "fail",
        "sparkle", "swoosh", "pop", "record_scratch", "tension", "error"]


def _speech(*texts: str, gap: float = 3.0) -> tuple[list[Utterance], float]:
    """텍스트들을 일정 간격으로 늘어놓는다."""
    out: list[Utterance] = []
    t = 1.0
    for text in texts:
        out.append(Utterance(t, t + 2.0, text))
        t += gap
    return out, t + 2.0


def _build(utterances, total, **kw):
    kw.setdefault("sfx_names", _SFX)
    return autocue.build_cuesheet(utterances, total, **kw)


def _texts(sheet) -> list[str]:
    return [c.text for c in sheet.captions]


# ---------------------------------------------------------------- 신호 감지


@pytest.mark.parametrize("line,expected", [
    ("와 진짜 대박이다", "헐 대박"),
    ("헐 이게 뭐야", "헐 대박"),
    ("됐다 성공했어요", "성공!"),
    ("아 망했다 이거", "실패"),
    ("어? 이게 왜 이러지", "?!"),
    ("ㅋㅋㅋ 뭐야", "(웃음)"),
    ("좀 무섭네요", "(긴장)"),
    ("음... 잘 모르겠는데", "(당황)"),
    ("아 짜증나 진짜", "(폭발)"),
    ("세상에 이런 일이", "어머"),
])
def test_detects_signal(line: str, expected: str):
    utterances, total = _speech(line)
    assert expected in _texts(_build(utterances, total))


def test_plain_line_gets_nothing():
    """평범한 정보 전달에는 자막을 붙이지 않는다."""
    utterances, total = _speech("오늘 날씨가 좋아서 밖에 나왔습니다")
    assert _build(utterances, total).captions == []


def test_detects_long_silence():
    utterances = [Utterance(1.0, 3.0, "그래서 말인데"),
                  Utterance(8.0, 10.0, "아무튼 그렇습니다")]
    texts = _texts(_build(utterances, 12.0))
    assert any("정적" in t for t in texts)


def test_short_gap_is_not_silence():
    utterances = [Utterance(1.0, 3.0, "그래서"), Utterance(3.5, 5.0, "그렇습니다")]
    assert not any("정적" in t for t in _texts(_build(utterances, 7.0)))


def test_higher_score_wins_on_one_line():
    """한 줄에 여러 규칙이 걸리면 강한 것 하나만 쓴다."""
    utterances, total = _speech("와 진짜 대박 성공했다!")
    sheet = _build(utterances, total)
    assert len(sheet.captions) == 1


# ---------------------------------------------------------------- 밀도


def test_density_controls_count():
    lines = ["와 대박", "헐 진짜", "됐다 성공", "아 망했다", "어? 뭐지",
             "ㅋㅋㅋ", "세상에", "짜증나", "무섭다", "모르겠어"]
    utterances, total = _speech(*lines, gap=6.0)   # 60초쯤

    low = len(_build(utterances, total, density="low").captions)
    normal = len(_build(utterances, total, density="normal").captions)
    high = len(_build(utterances, total, density="high").captions)
    assert low < normal <= high


def test_captions_do_not_overlap():
    lines = ["와 대박"] * 12
    utterances, total = _speech(*lines, gap=1.2)   # 일부러 촘촘하게
    ordered = sorted(_build(utterances, total, density="high").captions,
                     key=lambda c: c.start)
    for a, b in zip(ordered, ordered[1:]):
        assert b.start >= a.end


def test_captions_stay_inside_video():
    utterances, total = _speech("와 대박", "됐다 성공")
    for cap in _build(utterances, total).captions:
        assert 0.0 <= cap.start < cap.end <= total


# ---------------------------------------------------------------- 균형


def test_emphasis_is_rationed():
    """강조가 남발되면 화면이 온통 큰 빨간 자막이 된다."""
    lines = ["와 대박", "됐다 성공", "아 망했다", "헐 진짜", "성공했다"] * 3
    utterances, total = _speech(*lines, gap=4.0)
    sheet = _build(utterances, total, density="high")

    counts = Counter(c.style for c in sheet.captions)
    assert counts["emphasis"] <= max(1, round(total / 45.0))
    assert counts["reaction"] > counts["emphasis"], "리액션이 기본이어야 한다"


def test_positions_are_rotated():
    lines = ["와 대박", "됐다 성공", "어? 뭐지", "ㅋㅋㅋ", "세상에"]
    utterances, total = _speech(*lines, gap=4.0)
    positions = {c.position for c in _build(utterances, total, density="high").captions}
    assert len(positions) > 1, "자막이 한 자리에만 몰린다"


# ---------------------------------------------------------------- 부속 큐


def test_sfx_lands_on_caption():
    utterances, total = _speech("와 진짜 대박이다")
    sheet = _build(utterances, total)
    starts = {round(c.start, 3) for c in sheet.captions}
    assert sheet.sfx
    assert all(round(s.time, 3) in starts for s in sheet.sfx)


def test_unknown_sfx_is_not_used():
    """라이브러리에 없는 소리를 큐시트에 넣으면 안 된다."""
    utterances, total = _speech("와 진짜 대박이다")
    sheet = _build(utterances, total, sfx_names=["ding"])
    assert all(s.name == "ding" for s in sheet.sfx)


def test_no_sfx_library_means_no_sfx():
    utterances, total = _speech("와 진짜 대박이다")
    assert _build(utterances, total, sfx_names=[]).sfx == []


def test_zoom_only_on_strong_moments():
    utterances, total = _speech("와 진짜 대박이다", "오늘 날씨가 좋네요")
    sheet = _build(utterances, total)
    assert len(sheet.zooms) == 1


def test_zooms_do_not_overlap():
    lines = ["와 대박"] * 6
    utterances, total = _speech(*lines, gap=1.5)
    zooms = _build(utterances, total, density="high").zooms
    for a, b in zip(zooms, zooms[1:]):
        assert b.start >= a.end


def test_transitions_only_at_boundaries():
    utterances, total = _speech("와 대박", "됐다 성공")
    sheet = _build(utterances, total, transition_names=["dissolve"],
                   boundaries=[5.0, 9.0])
    assert {t.time for t in sheet.transitions} <= {5.0, 9.0}


def test_no_boundaries_no_transitions():
    utterances, total = _speech("와 대박")
    sheet = _build(utterances, total, transition_names=["dissolve"], boundaries=[])
    assert sheet.transitions == []


def test_effects_are_rare():
    lines = ["와 대박", "됐다 성공", "아 망했다", "헐 진짜"] * 3
    utterances, total = _speech(*lines, gap=4.0)
    sheet = _build(utterances, total, density="high",
                   effect_names=["flash", "shake", "glitch"])
    assert len(sheet.effects) <= 3, "화면효과는 남발하면 촌스럽다"


def test_empty_input():
    sheet = _build([], 30.0)
    assert sheet.captions == [] and sheet.sfx == []


def test_result_is_deterministic():
    """같은 입력이면 항상 같은 결과여야 예측 가능하다."""
    utterances, total = _speech("와 대박", "됐다 성공", "어? 뭐지")
    assert _build(utterances, total).to_json() == _build(utterances, total).to_json()
