"""자막 스타일 예시 추출 테스트."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung import styleref  # noqa: E402


def _draft(texts: list[str]) -> dict:
    """캡컷 초안의 텍스트 소재 부분만 흉내 낸 최소 구조."""
    return {
        "materials": {
            "texts": [
                {"id": str(i), "content": json.dumps({"text": t}, ensure_ascii=False)}
                for i, t in enumerate(texts)
            ]
        }
    }


def _write(tmp_path: Path, texts: list[str]) -> Path:
    p = tmp_path / "draft_content.json"
    p.write_text(json.dumps(_draft(texts), ensure_ascii=False), encoding="utf-8")
    return p


def test_extracts_captions(tmp_path: Path):
    p = _write(tmp_path, ["?!", "이게 되네", "(정적)"])
    assert styleref.extract_from_draft(p) == ["?!", "이게 되네", "(정적)"]


def test_accepts_folder_path(tmp_path: Path):
    _write(tmp_path, ["당황"])
    assert styleref.extract_from_draft(tmp_path) == ["당황"]


def test_drops_dialogue_lines(tmp_path: Path):
    """대사 자막을 예시로 쓰면 문체가 망가진다. 길이로 걸러낸다."""
    p = _write(tmp_path, [
        "?!",
        "이게 되네",
        "안녕하세요 오늘은 오랜만에 인사드립니다",       # 대사
        "그래서 제가 어제 뭘 했냐면요",                  # 대사
    ])
    assert styleref.extract_from_draft(p) == ["?!", "이게 되네"]


def test_keeps_typical_variety_captions(tmp_path: Path):
    """실제 예능 자막 길이는 살아남아야 한다."""
    captions = ["?!", "당황", "(진지)", "이게 되네", "결국 실패", "표정 굳음", "3초 정적"]
    p = _write(tmp_path, captions)
    assert styleref.extract_from_draft(p) == captions


def test_dedupes(tmp_path: Path):
    p = _write(tmp_path, ["당황", "당황", "?!"])
    assert styleref.extract_from_draft(p) == ["당황", "?!"]


def test_normalizes_whitespace(tmp_path: Path):
    p = _write(tmp_path, ["이게   되네\n진짜"])
    assert styleref.extract_from_draft(p) == ["이게 되네 진짜"]


def test_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        styleref.extract_from_draft(tmp_path / "없음")


def test_encrypted_draft_gives_useful_error(tmp_path: Path):
    """최신 캡컷은 초안을 암호화한다. 그때 무슨 상황인지 알려줘야 한다."""
    p = tmp_path / "draft_content.json"
    p.write_bytes(b"\x00\x01\x02\xff\xfe not json at all")
    with pytest.raises(ValueError, match="암호화"):
        styleref.extract_from_draft(p)


def test_skips_malformed_text_material(tmp_path: Path):
    p = tmp_path / "draft_content.json"
    p.write_text(
        json.dumps({"materials": {"texts": [
            {"id": "1", "content": "이건 JSON 이 아님"},
            {"id": "2", "content": json.dumps({"text": "정상"})},
            {"id": "3"},
        ]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert styleref.extract_from_draft(p) == ["정상"]


def test_save_and_load_roundtrip(tmp_path: Path):
    out = styleref.save(["?!", "당황", "이게 되네"], tmp_path / "ref.txt", source="테스트")
    body = out.read_text(encoding="utf-8")
    assert "# 자막 스타일 예시" in body
    assert "# 출처: 테스트" in body
    assert styleref.load(out) == ["?!", "당황", "이게 되네"]


def test_load_ignores_comments_and_blanks(tmp_path: Path):
    p = tmp_path / "ref.txt"
    p.write_text("# 주석\n\n  ?!  \n\n# 또 주석\n당황\n", encoding="utf-8")
    assert styleref.load(p) == ["?!", "당황"]


def test_load_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        styleref.load(tmp_path / "없음.txt")
