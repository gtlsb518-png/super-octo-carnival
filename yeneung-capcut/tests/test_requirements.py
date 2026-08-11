"""requirements.txt 가 실제로 설치 가능한지 본다.

한 번 `pycapcut>=0.2.0` 이라고 적어 놓고 넘어간 적이 있다. 그런 버전은
세상에 없어서, 받은 사람이 실행하자마자 설치가 통째로 실패했다.
여기서 돌아가는 환경에는 이미 깔려 있으니 아무도 눈치채지 못했다.

그래서 **지금 깔려 있는 것이 요구사항을 만족하는지** 검사한다.
네트워크를 쓰지 않고도 그때의 실수를 그대로 잡아낸다.
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

packaging = pytest.importorskip("packaging")
from packaging.requirements import Requirement  # noqa: E402


def _requirements(name: str) -> list[Requirement]:
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return [
        Requirement(line.strip())
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


@pytest.mark.parametrize("req", _requirements("requirements.txt"), ids=str)
def test_pinned_version_is_installable(req: Requirement):
    """적어둔 버전 범위 안에 실제 배포본이 있는가."""
    try:
        have = version(req.name)
    except PackageNotFoundError:
        pytest.skip(f"{req.name} 이 이 환경에 없어 확인할 수 없습니다")

    assert req.specifier.contains(have, prereleases=True), (
        f"requirements.txt 는 {req} 를 요구하는데 실제로 깔린 건 {have} 입니다.\n"
        f"존재하지 않는 버전을 적었을 가능성이 높습니다."
    )


def test_api_extra_is_separate():
    """anthropic 은 선택 사항이라 기본 설치에 끼어 있으면 안 된다."""
    names = {r.name for r in _requirements("requirements.txt")}
    assert "anthropic" not in names
    assert "anthropic" in {r.name for r in _requirements("requirements-api.txt")}
