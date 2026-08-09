"""효과음 라이브러리.

assets/sfx/manifest.json 에 논리 이름 → 파일 + 설명을 적어두면,
큐시트를 만드는 Claude 가 그 이름들 중에서만 고른다.
manifest.json 이 없으면 폴더의 오디오 파일을 파일명 기준으로 자동 등록한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class Sfx:
    name: str
    path: Path
    description: str


class SfxLibrary:
    def __init__(self, entries: dict[str, Sfx], root: Path) -> None:
        self._entries = entries
        self.root = root

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())

    def get(self, name: str) -> Sfx | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def catalog(self, limit: int | None = None) -> str:
        """프롬프트에 넣을 '이름: 설명' 목록."""
        items = sorted(self, key=lambda s: s.name)
        if limit is not None:
            items = items[:limit]
        return "\n".join(f"- {s.name}: {s.description}" for s in items)


def load_library(root: str | Path) -> SfxLibrary:
    """효과음 폴더를 읽는다. 폴더가 없으면 빈 라이브러리."""
    base = Path(root)
    if not base.is_dir():
        return SfxLibrary({}, base)

    entries: dict[str, Sfx] = {}
    manifest = base / MANIFEST_NAME

    if manifest.exists():
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        for name, item in raw.items():
            if isinstance(item, str):
                item = {"file": item, "desc": name}
            path = base / item["file"]
            if not path.exists():
                continue  # 매니페스트에 적혔지만 파일이 없으면 조용히 건너뜀
            entries[name] = Sfx(name, path.resolve(), item.get("desc", name))
    else:
        # 매니페스트가 없으면 하위 폴더까지 훑어 파일명 그대로 등록한다.
        # 설명이 없으니 Claude 가 언제 쓸지 판단하기 어렵다 — `sfx scan` 을 권한다.
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _AUDIO_EXT:
                continue
            name = path.stem
            if name in entries:  # 다른 폴더에 같은 이름이 있으면 폴더명을 붙인다
                name = f"{path.parent.name}_{path.stem}"
            entries[name] = Sfx(name, path.resolve(), path.stem)

    return SfxLibrary(entries, base)


def missing_files(root: str | Path) -> list[str]:
    """매니페스트에 있으나 실제 파일이 없는 항목들."""
    base = Path(root)
    manifest = base / MANIFEST_NAME
    if not manifest.exists():
        return []
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    out: list[str] = []
    for name, item in raw.items():
        rel = item if isinstance(item, str) else item.get("file", "")
        if not (base / rel).exists():
            out.append(f"{name} -> {rel}")
    return out
