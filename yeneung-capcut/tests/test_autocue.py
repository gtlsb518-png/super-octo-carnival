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

from yeneung import autocue, styles  # noqa: E402
from yeneung.transcribe import Utterance  # noqa: E402

_SFX = ["ding", "drum", "boing", "crickets", "correct", "fail",
        "sparkle", "swoosh", "pop", "record_scratch", "tension", "error"]

#: 실제 캡컷에서 쓸 수 있는 것들 (styles.available_* 가 돌려주는 것과 같은 이름)
_FX = list(styles.EFFECT_CANDIDATES)
_TRANS = list(styles.TRANSITION_CANDIDATES)


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


def test_every_boundary_gets_a_transition():
    """컷이 있으면 전환이 있어야 한다. 빈 경계가 있으면 툭툭 끊긴다."""
    utterances, total = _speech("와 대박", "됐다 성공", gap=8.0)
    bounds = [4.0, 8.0, 12.0, 16.0, 20.0]
    sheet = _build(utterances, total, transition_names=_TRANS, boundaries=bounds)
    assert {round(t.time, 2) for t in sheet.transitions} == set(bounds)


def test_transitions_are_not_all_identical():
    utterances, total = _speech("와 대박", "됐다 성공", "아 망했다", gap=8.0)
    sheet = _build(utterances, total, transition_names=_TRANS,
                   boundaries=[4.0, 8.0, 12.0, 16.0, 20.0, 24.0])
    assert len({t.name for t in sheet.transitions}) > 1, "전환이 한 종류뿐이다"


def test_transition_matches_the_moment():
    """실패 장면에 반짝이가 깔리면 안 된다."""
    utterances = [Utterance(5.0, 7.0, "아 망했다 진짜")]
    sheet = _build(utterances, 20.0, transition_names=_TRANS, boundaries=[5.1])
    assert sheet.transitions[0].name in ("fade_black", "glitch_color")


def test_no_boundaries_no_transitions():
    utterances, total = _speech("와 대박")
    sheet = _build(utterances, total, transition_names=["dissolve"], boundaries=[])
    assert sheet.transitions == []


def test_unknown_transition_is_not_used():
    """후보가 이 캡컷 버전에 없으면 있는 것으로 대체해야 한다."""
    utterances, total = _speech("아 망했다")
    sheet = _build(utterances, total, transition_names=["dissolve"],
                   boundaries=[4.0, 8.0])
    assert all(t.name == "dissolve" for t in sheet.transitions)


def test_effects_scale_with_length():
    """길이와 무관하게 3개로 자르면 긴 영상이 밋밋해진다."""
    short, s_total = _speech(*(["와 대박", "됐다 성공"] * 2), gap=4.0)      # 약 18초
    long_, l_total = _speech(*(["와 대박", "됐다 성공"] * 15), gap=4.0)     # 약 122초
    n_short = len(_build(short, s_total, density="high", effect_names=_FX).effects)
    n_long = len(_build(long_, l_total, density="high", effect_names=_FX).effects)
    assert n_long > n_short, f"긴 영상인데 효과가 안 늘었다 ({n_short} → {n_long})"


def test_effects_are_not_all_identical():
    lines = ["와 대박", "됐다 성공", "아 망했다", "어? 뭐지"] * 4
    utterances, total = _speech(*lines, gap=4.0)
    sheet = _build(utterances, total, density="high", effect_names=_FX)
    assert len({e.name for e in sheet.effects}) > 1, "화면효과가 한 종류뿐이다"


def test_effect_matches_the_moment():
    utterances = [Utterance(5.0, 7.0, "됐다 드디어 성공했어요")]
    sheet = _build(utterances, 30.0, effect_names=_FX)
    assert sheet.effects[0].name in ("firework", "confetti", "sparkle")


def test_effects_do_not_flood_the_screen():
    lines = ["와 대박", "됐다 성공"] * 20
    utterances, total = _speech(*lines, gap=1.5)
    sheet = _build(utterances, total, density="high", effect_names=_FX)
    assert len(sheet.effects) <= len(sheet.captions), "효과가 자막보다 많으면 촌스럽다"


# ---------------------------------------------------------------- 퇴장 애니메이션


def test_captions_have_an_outro():
    """등장만 있고 퇴장이 없으면 자막이 뚝 끊기듯 사라진다."""
    lines = ["와 대박", "됐다 성공", "아 망했다", "ㅋㅋㅋ", "무섭다"]
    utterances, total = _speech(*lines, gap=4.0)
    caps = _build(utterances, total, density="high").captions
    assert caps
    assert all(c.outro and c.outro != "default" for c in caps)


def test_outro_matches_the_moment():
    utterances = [Utterance(5.0, 7.0, "아 망했다 진짜")]
    caps = _build(utterances, 20.0).captions
    assert caps[0].outro == "glitch"


def test_empty_input():
    sheet = _build([], 30.0)
    assert sheet.captions == [] and sheet.sfx == []


def test_result_is_deterministic():
    """같은 입력이면 항상 같은 결과여야 예측 가능하다."""
    utterances, total = _speech("와 대박", "됐다 성공", "어? 뭐지")
    assert _build(utterances, total).to_json() == _build(utterances, total).to_json()


# ------------------------------------------------- 메인 클립 기준 배치


def _clips(n: int, length: float = 4.0) -> list[tuple[float, float]]:
    return [(i * length, (i + 1) * length) for i in range(n)]


def _talky(n: int, length: float = 4.0) -> list[Utterance]:
    """클립마다 대사가 하나씩 있는 상황."""
    lines = ["와 대박", "됐다 성공", "아 망했다", "어? 뭐지", "ㅋㅋㅋ 웃겨",
             "오늘은 날씨가 참 좋습니다", "세상에", "무섭다", "짜증나", "진짜?"]
    return [Utterance(i * length + 0.5, i * length + 2.5, lines[i % len(lines)])
            for i in range(n)]


def test_every_clip_gets_a_caption():
    """클립 기준이면 빈 클립이 없어야 한다."""
    clips = _clips(10)
    sheet = _build(_talky(10), 40.0, segments=clips, effect_names=_FX)
    for start, end in clips:
        assert any(start - 0.01 <= c.start < end for c in sheet.captions), \
            f"{start:.1f}~{end:.1f} 클립이 비었다"


def test_every_clip_gets_an_effect():
    clips = _clips(10)
    sheet = _build(_talky(10), 40.0, segments=clips, effect_names=_FX)
    for start, end in clips:
        assert any(start - 0.01 <= e.start < end for e in sheet.effects), \
            f"{start:.1f}~{end:.1f} 클립에 효과가 없다"


def test_effect_is_aligned_to_its_caption_clip():
    """효과는 자막 클립과 같은 자리·같은 길이여야 캡컷에서 같이 잡힌다."""
    sheet = _build(_talky(6), 24.0, segments=_clips(6), effect_names=_FX)
    spans = {(round(c.start, 4), round(c.end, 4)) for c in sheet.captions}
    assert sheet.effects
    for fx in sheet.effects:
        assert (round(fx.start, 4), round(fx.end, 4)) in spans


def test_caption_stays_inside_its_clip():
    clips = _clips(6)
    sheet = _build(_talky(6), 24.0, segments=clips)
    for cap in sheet.captions:
        home = [c for c in clips if c[0] - 0.01 <= cap.start < c[1]]
        assert home, f"{cap.start} 자막이 어느 클립에도 속하지 않는다"
        assert cap.end <= home[0][1] + 1e-6, "자막이 클립 밖으로 넘친다"


def test_plain_line_becomes_a_quote():
    """규칙에 안 걸려도 실제로 한 말을 줄여서 띄운다."""
    utterances = [Utterance(0.5, 3.0, "오늘은 날씨가 참 좋아서 밖에 나왔습니다")]
    caps = _build(utterances, 4.0, segments=[(0.0, 4.0)]).captions
    assert len(caps) == 1
    assert caps[0].text in "오늘은 날씨가 참 좋아서 밖에 나왔습니다"
    assert len(caps[0].text) <= 12


def test_quote_is_never_invented():
    """없는 말을 지어내면 안 된다."""
    said = "이번에는 조금 다른 방법으로 해보겠습니다"
    caps = _build([Utterance(0.5, 3.0, said)], 4.0, segments=[(0.0, 4.0)]).captions
    assert caps[0].text in said


def test_silent_clip_gets_nothing():
    """말이 없는 클립엔 지어낼 말이 없다."""
    utterances = [Utterance(0.5, 2.5, "와 대박")]
    sheet = _build(utterances, 20.0, segments=[(0.0, 4.0), (10.0, 14.0)])
    assert len(sheet.captions) == 1


def test_tiny_clips_are_skipped():
    """0.2초짜리 클립에 자막을 우겨넣으면 깜빡이고 만다."""
    utterances = [Utterance(0.1, 0.2, "와 대박"), Utterance(4.5, 6.0, "됐다 성공")]
    sheet = _build(utterances, 10.0, segments=[(0.0, 0.3), (4.0, 10.0)])
    assert all(c.start >= 4.0 for c in sheet.captions)


def test_repeated_signal_varies_its_effect():
    """같은 말이 반복돼도 같은 효과가 계속 깔리면 안 된다."""
    utterances = [Utterance(i * 4 + 0.5, i * 4 + 2.5, "와 진짜 대박이다") for i in range(6)]
    sheet = _build(utterances, 24.0, segments=_clips(6), effect_names=_FX)
    assert len({e.name for e in sheet.effects}) > 1


def test_clip_mode_still_rations_emphasis():
    utterances = [Utterance(i * 4 + 0.5, i * 4 + 2.5, "와 대박") for i in range(10)]
    sheet = _build(utterances, 40.0, segments=_clips(10))
    counts = Counter(c.style for c in sheet.captions)
    assert counts["emphasis"] <= max(1, round(40.0 / 45.0))


def test_density_controls_which_clips_qualify():
    """짧은 클립은 low 에서 빠지고 high 에서 들어온다."""
    clips = [(i * 1.0, i * 1.0 + 0.9) for i in range(10)]
    utterances = [Utterance(i * 1.0 + 0.1, i * 1.0 + 0.8, "와 대박") for i in range(10)]
    low = len(_build(utterances, 10.0, segments=clips, density="low").captions)
    high = len(_build(utterances, 10.0, segments=clips, density="high").captions)
    assert low < high
