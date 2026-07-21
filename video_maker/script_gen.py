"""주제 -> 대본 생성.

ANTHROPIC_API_KEY 가 있으면 Claude로 고품질 대본을 만들고,
없으면 템플릿 기반 대본으로도 동작한다(오프라인 데모 가능).
"""
from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass, asdict


@dataclass
class Scene:
    """대본의 한 장면."""

    headline: str      # 화면 중앙에 크게 표시되는 짧은 문구
    narration: str     # 성우가 읽는 문장(자막으로도 사용)


@dataclass
class Script:
    """영상 한 편의 전체 대본."""

    topic: str
    title: str
    scenes: list[Scene]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Script":
        scenes = [Scene(**s) for s in d["scenes"]]
        return cls(topic=d["topic"], title=d["title"], scenes=scenes)


SYSTEM_PROMPT = """너는 한국어 숏폼(YouTube Shorts) 영상 대본 작가다.
주어진 주제로 30~50초 분량의 몰입감 있는 대본을 만든다.
반드시 아래 JSON 스키마만 출력한다(설명·마크다운 금지):

{
  "title": "영상 제목(짧고 후킹)",
  "scenes": [
    {"headline": "화면에 크게 뜰 6~12자 핵심 문구",
     "narration": "성우가 읽을 1~2문장. 자연스러운 구어체."}
  ]
}

규칙:
- scenes 는 5~7개.
- 첫 장면은 시청자를 붙잡는 강한 훅.
- 마지막 장면은 마무리/한 줄 요약 또는 질문.
- narration 은 각 15~35자 내외로 간결하게.
"""


def _generate_with_claude(topic: str) -> Script:
    """Claude API로 대본 생성. 실패 시 예외를 던진다."""
    from anthropic import Anthropic  # lazy import

    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    msg = client.messages.create(
        model=os.environ.get("VIDEO_MAKER_MODEL", "claude-opus-4-8"),
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"주제: {topic}"}],
    )
    raw = "".join(block.text for block in msg.content if block.type == "text").strip()
    # 코드펜스가 섞여 오면 벗겨낸다.
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
    data = json.loads(raw)
    scenes = [Scene(headline=s["headline"], narration=s["narration"]) for s in data["scenes"]]
    return Script(topic=topic, title=data.get("title", topic), scenes=scenes)


def _generate_fallback(topic: str) -> Script:
    """API 키가 없을 때 쓰는 템플릿 대본.

    주제만으로 그럴듯한 5장면 구조(훅→배경→핵심1→핵심2→마무리)를 만든다.
    실제 콘텐츠 품질은 낮지만 파이프라인을 끝까지 돌려볼 수 있다.
    """
    t = topic.strip()
    scenes = [
        Scene(headline="이거 아세요?",
              narration=f"오늘은 '{t}'에 대해 이야기해 볼게요."),
        Scene(headline="왜 중요할까",
              narration=f"{t}, 생각보다 우리 일상과 깊이 연결되어 있어요."),
        Scene(headline="핵심 포인트 ①",
              narration=f"먼저 {t}의 기본 원리를 이해하는 게 중요해요."),
        Scene(headline="핵심 포인트 ②",
              narration=f"그리고 {t}를 실제로 어떻게 활용하는지 살펴봐요."),
        Scene(headline="정리하면",
              narration=f"{t}, 오늘 내용이 도움이 되셨다면 구독과 좋아요!"),
    ]
    return Script(topic=t, title=f"{t} 한눈에 정리", scenes=scenes)


def generate_script(topic: str, *, use_api: bool | None = None) -> Script:
    """주제로부터 대본을 생성한다.

    use_api=None(기본): API 키가 있으면 Claude, 없으면 fallback.
    use_api=False: 무조건 fallback.
    """
    if use_api is None:
        use_api = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_api:
        try:
            return _generate_with_claude(topic)
        except Exception as e:  # noqa: BLE001
            print(f"[script_gen] Claude 생성 실패 → 템플릿 사용: {e}")
    return _generate_fallback(topic)


def wrap_text(text: str, width: int) -> str:
    """자막용 줄바꿈 헬퍼."""
    return "\n".join(textwrap.wrap(text, width=width)) or text
