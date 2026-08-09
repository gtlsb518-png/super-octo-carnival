"""자막 스타일 예시.

Claude 를 학습(파인튜닝)시킬 수는 없다. 대신 프롬프트에 실제 자막 예시를
넣어주면 문체와 호흡이 눈에 띄게 달라진다.

예시로 가장 좋은 건 남의 영상이 아니라 **내가 예전에 캡컷에서 직접 단 자막**이다.
캡컷 초안을 읽어 텍스트만 뽑아낼 수 있으므로 그걸 그대로 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path

#: 이보다 긴 줄은 예능 자막이 아니라 대사 자막으로 보고 제외.
#: 예능 자막은 대부분 2~12자, 대사 한 줄은 15자를 넘는다. 경계에서는
#: 버리는 쪽을 택한다 — 대사가 예시에 섞이면 문체가 대사처럼 길어진다.
MAX_CAPTION_CHARS = 14


def load(path: str | Path) -> list[str]:
    """예시 파일을 읽는다. 한 줄에 자막 하나, `#` 로 시작하면 주석."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"스타일 예시 파일이 없습니다: {p}")

    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def extract_from_draft(draft_content: str | Path) -> list[str]:
    """캡컷 초안(draft_content.json)에서 자막 문구를 뽑는다.

    긴 줄(대사 자막으로 추정)과 중복은 걸러낸다.
    """
    p = Path(draft_content)
    if p.is_dir():
        p = p / "draft_content.json"
    if not p.exists():
        raise FileNotFoundError(f"캡컷 초안을 찾을 수 없습니다: {p}")

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"초안을 읽지 못했습니다: {p}\n"
            "최신 캡컷은 프로젝트 파일을 암호화합니다. 이 경우 자동 추출은 불가능하니 "
            "자막 문구를 텍스트 파일에 직접 적어 --style-ref 로 넘겨주세요."
        ) from exc

    texts: list[str] = []
    for material in data.get("materials", {}).get("texts", []):
        content = material.get("content")
        if not content:
            continue
        try:
            text = json.loads(content).get("text", "")
        except json.JSONDecodeError:
            continue
        text = " ".join(text.split())
        if text:
            texts.append(text)

    return _filter(texts)


def extract_animations(draft_content: str | Path) -> list[str]:
    """초안에서 실제로 쓴 자막 등장 애니메이션 이름을 세어 많이 쓴 순으로."""
    from collections import Counter

    p = Path(draft_content)
    if p.is_dir():
        p = p / "draft_content.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []

    counter: Counter[str] = Counter()
    for material in data.get("materials", {}).get("material_animations", []):
        for anim in material.get("animations", []):
            if anim.get("type") == "in" and anim.get("name"):
                counter[anim["name"]] += 1
    return [name for name, _ in counter.most_common()]


def _filter(texts: list[str]) -> list[str]:
    """예능 자막처럼 보이는 것만 남기고 중복을 없앤다."""
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if len(text) > MAX_CAPTION_CHARS:
            continue  # 대사 자막
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def save(
    lines: list[str], path: str | Path, *, source: str = "",
    animations: list[str] | None = None,
) -> Path:
    """예시를 파일로 저장한다. 사람이 직접 손보기 좋게 주석을 붙인다."""
    header = [
        "# 자막 스타일 예시",
        "# 한 줄에 자막 하나. '#' 로 시작하는 줄은 무시됩니다.",
        "# 문구가 그대로 복사되는 게 아니라, 길이와 말투를 참고하는 용도입니다.",
    ]
    if source:
        header.append(f"# 출처: {source}")
    if animations:
        header.append(f"# 이 프로젝트에서 쓴 자막 애니메이션: {', '.join(animations[:6])}")
    header.append("")

    p = Path(path)
    p.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    return p
