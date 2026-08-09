"""직접 쓴 큐시트 JSON 읽기 테스트.

사람이 손으로 쓰는 파일이므로, 틀렸을 때 **어디가 왜 틀렸는지** 알려주는 게
형식을 엄격히 지키는 것만큼 중요하다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung.cuesheet import Cuesheet, CuesheetError, check_names, parse  # noqa: E402


def _err(data) -> str:
    with pytest.raises(CuesheetError) as info:
        parse(data)
    return str(info.value)


# ---------------------------------------------------------------- 정상 입력


def test_minimal_caption_only():
    sheet = parse({"captions": [{"start": 1.0, "end": 2.0, "text": "?!"}]})
    assert len(sheet.captions) == 1
    assert sheet.captions[0].style == "reaction"     # 기본값
    assert sheet.captions[0].position == "default"
    assert sheet.sfx == [] and sheet.zooms == [] and sheet.effects == []


def test_all_sections():
    sheet = parse({
        "captions": [{"start": 1, "end": 2, "text": "가", "style": "emphasis", "position": "top"}],
        "sfx": [{"time": 1, "name": "ding"}],
        "zooms": [{"start": 3, "end": 5, "scale": 1.3}],
        "effects": [{"start": 3, "end": 3.5, "name": "flash"}],
    })
    assert sheet.captions[0].style == "emphasis"
    assert sheet.sfx[0].name == "ding"
    assert sheet.zooms[0].scale == pytest.approx(1.3)
    assert sheet.effects[0].name == "flash"


def test_integers_become_floats():
    sheet = parse({"captions": [{"start": 1, "end": 2, "text": "가"}]})
    assert isinstance(sheet.captions[0].start, float)


def test_empty_object_is_valid():
    sheet = parse({})
    assert sheet.captions == []


def test_null_section_is_ignored():
    assert parse({"captions": None}).captions == []


# ---------------------------------------------------------------- 오류 안내


def test_reports_every_problem_at_once():
    """한 번 돌려서 전부 알려줘야 왕복이 줄어든다."""
    message = _err({
        "captions": [{"start": 1}, {"start": 2, "end": 1, "text": "가"}],
        "sfx": [{"time": "글자", "name": "ding"}],
    })
    assert "3군데" in message
    assert "빠진 필드" in message
    assert "끝(1.0)이 시작(2.0)보다 뒤여야" in message
    assert "숫자여야 합니다" in message


def test_suggests_section_typo():
    assert "'captions' 를 쓰려던" in _err({"caption": []})


def test_suggests_field_typo():
    assert "'style' 를 쓰려던" in _err(
        {"captions": [{"start": 1, "end": 2, "text": "가", "styl": "reaction"}]}
    )


def test_checks_inside_mistyped_section():
    """섹션 이름이 틀렸어도 그 안의 오류까지 같이 잡아준다."""
    message = _err({"caption": [{"start": 1, "end": 2}]})
    assert "'captions' 를 쓰려던" in message
    assert "빠진 필드 text" in message


def test_rejects_negative_time():
    assert "음수는 안 됩니다" in _err({"sfx": [{"time": -1, "name": "ding"}]})


def test_rejects_non_list_section():
    assert "목록([ ])이어야" in _err({"captions": {"start": 1}})


def test_rejects_non_object_item():
    assert "객체({ })여야" in _err({"captions": ["문자열"]})


def test_rejects_top_level_list():
    assert "최상위가 객체" in _err([{"start": 1}])


def test_rejects_bool_as_number():
    """True 는 파이썬에선 1 이지만 시각으로 쓰면 실수다."""
    assert "숫자여야 합니다" in _err({"sfx": [{"time": True, "name": "ding"}]})


def test_rejects_non_string_text():
    assert "문자열이어야 합니다" in _err({"captions": [{"start": 1, "end": 2, "text": 123}]})


def test_error_shows_example():
    assert '"captions"' in _err({"caption": []})


def test_bad_json_file_gives_line_number(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text('{\n  "captions": [\n    {"start": 1 "end": 2}\n  ]\n}', encoding="utf-8")
    with pytest.raises(CuesheetError) as info:
        Cuesheet.load(p)
    message = str(info.value)
    assert "JSON 형식이 아닙니다" in message
    assert "3번째 줄" in message


def test_load_roundtrip(tmp_path: Path):
    p = tmp_path / "cue.json"
    p.write_text(
        json.dumps({"captions": [{"start": 1, "end": 2, "text": "이게 되네"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert Cuesheet.load(p).captions[0].text == "이게 되네"


# ---------------------------------------------------------------- 이름 확인


_KNOWN = {
    "style_names": ["reaction", "emphasis"],
    "sfx_names": ["ding", "drum"],
    "effect_names": ["flash", "shake"],
    "positions": ["top", "upper"],
}


def test_all_names_valid_gives_no_warning():
    sheet = parse({
        "captions": [{"start": 1, "end": 2, "text": "가", "style": "emphasis", "position": "top"}],
        "sfx": [{"time": 1, "name": "ding"}],
        "effects": [{"start": 1, "end": 2, "name": "flash"}],
    })
    assert check_names(sheet, **_KNOWN) == []


def test_default_position_is_always_valid():
    sheet = parse({"captions": [{"start": 1, "end": 2, "text": "가"}]})
    assert check_names(sheet, **_KNOWN) == []


def test_warns_unknown_style_with_suggestion():
    sheet = parse({"captions": [{"start": 1, "end": 2, "text": "가", "style": "reation"}]})
    (warning,) = check_names(sheet, **_KNOWN)
    assert "reation" in warning and "'reaction' 를 쓰려던" in warning


def test_warns_unknown_sfx_and_effect():
    sheet = parse({
        "sfx": [{"time": 1, "name": "dingg"}],
        "effects": [{"start": 1, "end": 2, "name": "flashh"}],
    })
    warnings = check_names(sheet, **_KNOWN)
    assert len(warnings) == 2
    assert any("라이브러리에 없는 효과음" in w for w in warnings)
    assert any("쓸 수 없는 화면효과" in w for w in warnings)


def test_warning_points_at_the_index():
    sheet = parse({"captions": [
        {"start": 1, "end": 2, "text": "가"},
        {"start": 3, "end": 4, "text": "나", "style": "없는스타일"},
    ]})
    (warning,) = check_names(sheet, **_KNOWN)
    assert warning.startswith("captions[1]")
