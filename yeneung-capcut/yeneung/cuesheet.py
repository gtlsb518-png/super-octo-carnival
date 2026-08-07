"""Claude 로 예능 큐시트를 만든다.

입력: 컷 편집이 끝난 타임라인 기준의 대사 목록
출력: 예능 자막 / 효과음 / 줌 / 화면효과 큐 목록
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import POSITION_OVERRIDES, CuesheetConfig
from .transcribe import Utterance


# ---------------------------------------------------------------- 자료구조


@dataclass
class Caption:
    start: float
    end: float
    text: str
    style: str
    position: str = "default"


@dataclass
class SfxCue:
    time: float
    name: str


@dataclass
class ZoomCue:
    start: float
    end: float
    scale: float


@dataclass
class EffectCue:
    start: float
    end: float
    name: str


@dataclass
class Cuesheet:
    captions: list[Caption] = field(default_factory=list)
    sfx: list[SfxCue] = field(default_factory=list)
    zooms: list[ZoomCue] = field(default_factory=list)
    effects: list[EffectCue] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "captions": [asdict(c) for c in self.captions],
                "sfx": [asdict(c) for c in self.sfx],
                "zooms": [asdict(c) for c in self.zooms],
                "effects": [asdict(c) for c in self.effects],
            },
            ensure_ascii=False,
            indent=2,
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Cuesheet":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return Cuesheet(
            captions=[Caption(**c) for c in raw.get("captions", [])],
            sfx=[SfxCue(**c) for c in raw.get("sfx", [])],
            zooms=[ZoomCue(**c) for c in raw.get("zooms", [])],
            effects=[EffectCue(**c) for c in raw.get("effects", [])],
        )

    def summary(self) -> str:
        return (
            f"자막 {len(self.captions)}개 · 효과음 {len(self.sfx)}개 · "
            f"줌 {len(self.zooms)}개 · 화면효과 {len(self.effects)}개"
        )


# ---------------------------------------------------------------- 프롬프트

_DENSITY_GUIDE = {
    "low":    "1분당 예능 자막 4~6개, 효과음 2~4개, 줌 1~2개, 화면효과 0~1개",
    "normal": "1분당 예능 자막 8~12개, 효과음 5~8개, 줌 3~5개, 화면효과 1~2개",
    "high":   "1분당 예능 자막 14~20개, 효과음 9~14개, 줌 6~9개, 화면효과 3~5개",
}

_SYSTEM = """\
너는 한국 예능 프로그램의 자막 담당 편집자다. 컷 편집만 끝난 영상의 대사 스크립트를
받아서, 그 위에 얹을 예능 자막·효과음·줌·화면효과 큐시트를 만든다.

# 예능 자막의 문법
- 자막은 대사를 그대로 옮기는 게 아니다. 대사 자막은 이미 따로 깔린다.
  네가 만드는 건 제작진 시점의 '리액션'과 '해설'이다.
- 짧을수록 좋다. 대부분 2~8자. 긴 설명은 예능 자막이 아니다.
- 좋은 예: "?!", "당황", "(진지)", "이게 되네", "결국 실패", "표정 굳음", "3초 정적"
- 나쁜 예: "그는 매우 당황한 것처럼 보입니다", 대사를 그대로 반복하는 자막
- 웃음 포인트, 반전, 실수, 정적, 자화자찬, 예상 빗나감에 붙인다.
  평범하게 정보만 전달하는 구간에는 붙이지 않는다. 여백도 편집이다.

# 스타일 고르는 법
- reaction: 감탄·놀람·리액션. 가장 자주 쓴다.
- emphasis: 그 구간 전체의 핵심 한 방. 아껴 쓴다.
- narration: 제작진의 담백한 상황 설명.
- whisper: 속마음, 작은 목소리, 소심한 딴지.
- situation: 장소·시간·상황 안내 CG.

# 효과음과 줌
- 효과음은 자막이 뜨는 바로 그 순간에 겹쳐야 산다. 아무 데나 깔지 않는다.
- 줌은 표정이 결정적으로 변하는 순간에 0.8~2.5초로 짧게. 계속 당겨져 있으면 피곤하다.
- 화면효과는 정말 큰 반전에만. 남발하면 촌스러워진다.

# 지켜야 할 규칙
- 모든 시각은 제공된 타임라인의 초 단위이며, 대사 구간 안이나 그 바로 뒤여야 한다.
- 자막끼리 시간이 겹치면 안 된다. 최소 0.15초는 띄운다.
- 자막 길이는 0.6~2.5초.
- 효과음은 반드시 제공된 목록의 이름만 쓴다. 목록에 없으면 쓰지 않는다.
- 화면효과도 반드시 제공된 목록의 이름만 쓴다.
- 대사가 비어 있는 구간에 억지로 채우지 않는다.
"""

_USER_TEMPLATE = """\
프로그램 톤: {tone}
편집본 전체 길이: {total:.1f}초
이번에 작업할 구간: {win_start:.1f}초 ~ {win_end:.1f}초
목표 밀도: {density}

# 쓸 수 있는 효과음
{sfx_catalog}

# 쓸 수 있는 화면효과
{effect_catalog}

# 자막 위치 값
{position_list}

# 대사 스크립트
{context_block}{script}

위 '이번에 작업할 구간' 안에 들어가는 큐만 만들어라. 맥락으로 준 앞부분(<<맥락>> 표시)
에는 큐를 만들지 마라.
"""


def _schema(styles: Iterable[str], sfx_names: list[str], effect_names: list[str]) -> dict[str, Any]:
    """구조화 출력용 JSON 스키마. 선택지를 enum 으로 못박아 잘못된 이름을 막는다."""
    positions = ["default", *POSITION_OVERRIDES]

    caption = {
        "type": "object",
        "properties": {
            "start": {"type": "number", "description": "자막 시작 시각(초)"},
            "end": {"type": "number", "description": "자막 끝 시각(초)"},
            "text": {"type": "string", "description": "화면에 뜰 짧은 한국어 자막"},
            "style": {"type": "string", "enum": list(styles)},
            "position": {"type": "string", "enum": positions},
        },
        "required": ["start", "end", "text", "style", "position"],
        "additionalProperties": False,
    }
    sfx = {
        "type": "object",
        "properties": {
            "time": {"type": "number", "description": "효과음이 울릴 시각(초)"},
            "name": {"type": "string", "enum": sfx_names},
        },
        "required": ["time", "name"],
        "additionalProperties": False,
    }
    zoom = {
        "type": "object",
        "properties": {
            "start": {"type": "number"},
            "end": {"type": "number"},
            "scale": {"type": "number", "description": "확대 배율. 1.1~1.5 권장"},
        },
        "required": ["start", "end", "scale"],
        "additionalProperties": False,
    }
    effect = {
        "type": "object",
        "properties": {
            "start": {"type": "number"},
            "end": {"type": "number"},
            "name": {"type": "string", "enum": effect_names},
        },
        "required": ["start", "end", "name"],
        "additionalProperties": False,
    }

    props: dict[str, Any] = {
        "captions": {"type": "array", "items": caption},
        "zooms": {"type": "array", "items": zoom},
    }
    if sfx_names:
        props["sfx"] = {"type": "array", "items": sfx}
    if effect_names:
        props["effects"] = {"type": "array", "items": effect}

    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _script_lines(utterances: list[Utterance]) -> list[str]:
    return [f"[{u.start:.2f}-{u.end:.2f}] {u.text}" for u in utterances]


# ---------------------------------------------------------------- API 호출


class CuesheetError(RuntimeError):
    pass


def _call_claude(
    client: Any,
    cfg: CuesheetConfig,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """구조화 출력으로 한 번 호출한다. 실패하면 CuesheetError."""
    import anthropic

    params: dict[str, Any] = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": cfg.effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    }

    # 안전 분류기가 거절할 경우 서버 측에서 대체 모델로 자동 재시도하도록 켠다.
    beta_params = dict(params, betas=["server-side-fallback-2026-07-01"], fallbacks="default")

    try:
        with client.beta.messages.stream(**beta_params) as stream:
            message = stream.get_final_message()
    except anthropic.BadRequestError:
        # 이 계정/버전에서 fallbacks 베타를 못 쓰면 평범하게 호출
        with client.messages.stream(**params) as stream:
            message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise CuesheetError(
            "Claude 가 이 구간 처리를 거절했습니다. 해당 구간을 빼고 다시 시도해 주세요."
        )

    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise CuesheetError("Claude 응답에 본문이 없습니다.")
    return json.loads(text)


def generate(
    utterances: list[Utterance],
    total_duration: float,
    cfg: CuesheetConfig,
    style_names: list[str],
    sfx_names: list[str],
    effect_names: list[str],
    *,
    client: Any | None = None,
    progress: Any = None,
) -> Cuesheet:
    """대사 목록으로부터 큐시트를 만든다. 긴 영상은 나눠서 여러 번 호출한다."""
    if not utterances:
        return Cuesheet()

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - 설치 안내용
        raise CuesheetError("anthropic 이 설치되어 있지 않습니다. `pip install anthropic`") from exc

    client = client or anthropic.Anthropic()
    schema = _schema(style_names, sfx_names, effect_names)
    lines = _script_lines(utterances)

    sfx_catalog = "\n".join(f"- {n}" for n in sfx_names) or "(없음 — 효과음을 만들지 마라)"
    effect_catalog = "\n".join(f"- {n}" for n in effect_names) or "(없음 — 화면효과를 만들지 마라)"
    position_list = "\n".join(
        [f"- default: 스타일 기본 위치"] + [f"- {k}" for k in POSITION_OVERRIDES]
    )
    density = _DENSITY_GUIDE.get(cfg.density, _DENSITY_GUIDE["normal"])

    sheet = Cuesheet()
    step = max(1, cfg.chunk_lines)

    for begin in range(0, len(utterances), step):
        end = min(begin + step, len(utterances))
        ctx_start = max(0, begin - cfg.overlap_lines)

        context_block = ""
        if ctx_start < begin:
            context_block = "<<맥락 — 여기엔 큐를 만들지 마라>>\n" + \
                "\n".join(lines[ctx_start:begin]) + "\n<<여기부터 작업 구간>>\n"

        window = utterances[begin:end]
        user = _USER_TEMPLATE.format(
            tone=cfg.tone,
            total=total_duration,
            win_start=window[0].start,
            win_end=window[-1].end,
            density=density,
            sfx_catalog=sfx_catalog,
            effect_catalog=effect_catalog,
            position_list=position_list,
            context_block=context_block,
            script="\n".join(lines[begin:end]),
        )

        if progress:
            progress(begin // step + 1, (len(utterances) + step - 1) // step)

        data = _call_claude(client, cfg, _SYSTEM, user, schema)
        lo, hi = window[0].start, window[-1].end + 3.0

        sheet.captions += [
            Caption(**c) for c in data.get("captions", []) if lo <= c["start"] <= hi
        ]
        sheet.sfx += [SfxCue(**c) for c in data.get("sfx", []) if lo <= c["time"] <= hi]
        sheet.zooms += [ZoomCue(**c) for c in data.get("zooms", []) if lo <= c["start"] <= hi]
        sheet.effects += [
            EffectCue(**c) for c in data.get("effects", []) if lo <= c["start"] <= hi
        ]

    return sanitize(sheet, total_duration)


# ---------------------------------------------------------------- 후처리


def sanitize(sheet: Cuesheet, total: float, min_gap: float = 0.15) -> Cuesheet:
    """겹침·범위 초과·길이 이상을 정리한다. 모델 출력을 그대로 믿지 않는다."""
    captions = sorted(
        (c for c in sheet.captions if c.text.strip() and c.end > c.start),
        key=lambda c: c.start,
    )
    fixed: list[Caption] = []
    for cap in captions:
        cap.start = max(0.0, min(cap.start, total))
        cap.end = max(cap.start + 0.3, min(cap.end, total))
        if fixed and cap.start < fixed[-1].end + min_gap:
            shift = fixed[-1].end + min_gap
            if shift + 0.3 > total:
                continue  # 뒤로 밀 자리가 없으면 버린다
            cap.end += shift - cap.start
            cap.start = shift
            cap.end = min(cap.end, total)
        if cap.end > cap.start:
            fixed.append(cap)

    def clamp_range(items, total_: float):
        out = []
        for it in sorted(items, key=lambda x: x.start):
            it.start = max(0.0, min(it.start, total_))
            it.end = max(it.start + 0.2, min(it.end, total_))
            if it.end > it.start:
                out.append(it)
        return out

    return Cuesheet(
        captions=fixed,
        sfx=[s for s in sorted(sheet.sfx, key=lambda x: x.time) if 0.0 <= s.time <= total],
        zooms=_dedupe_overlap(clamp_range(sheet.zooms, total)),
        effects=_dedupe_overlap(clamp_range(sheet.effects, total)),
    )


def _dedupe_overlap(items: list) -> list:
    """겹치는 구간은 앞의 것만 남긴다 (줌/화면효과는 겹치면 이상해진다)."""
    out: list = []
    for it in items:
        if out and it.start < out[-1].end:
            continue
        out.append(it)
    return out
