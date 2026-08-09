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
    #: 등장 애니메이션 논리 이름. 비우면 스타일에 정해진 기본값을 쓴다
    anim: str = "default"


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
class TransitionCue:
    """컷과 컷 사이에 넣을 장면 전환."""

    time: float          # 전환이 걸릴 컷 경계 시각
    name: str            # dissolve, fade_black, ...
    duration: float = 0.5


@dataclass
class Cuesheet:
    captions: list[Caption] = field(default_factory=list)
    sfx: list[SfxCue] = field(default_factory=list)
    zooms: list[ZoomCue] = field(default_factory=list)
    effects: list[EffectCue] = field(default_factory=list)
    transitions: list[TransitionCue] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "captions": [asdict(c) for c in self.captions],
                "sfx": [asdict(c) for c in self.sfx],
                "zooms": [asdict(c) for c in self.zooms],
                "effects": [asdict(c) for c in self.effects],
                "transitions": [asdict(c) for c in self.transitions],
            },
            ensure_ascii=False,
            indent=2,
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Cuesheet":
        p = Path(path)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CuesheetError(
                f"{p} 을 읽지 못했습니다 — JSON 형식이 아닙니다.\n"
                f"  {exc.lineno}번째 줄: {exc.msg}\n"
                "  쉼표를 빠뜨렸거나 따옴표가 안 닫혔을 가능성이 큽니다."
            ) from exc
        return parse(raw, source=str(p))

    def summary(self) -> str:
        return (
            f"자막 {len(self.captions)}개 · 효과음 {len(self.sfx)}개 · "
            f"줌 {len(self.zooms)}개 · 화면효과 {len(self.effects)}개 · "
            f"전환 {len(self.transitions)}개"
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

# 자막 등장 애니메이션 (anim)
- 자막의 성격에 맞춰 고른다. 매번 같은 걸 쓰면 지루하지만, 한 영상에서
  너무 여러 종류를 쓰면 산만하다. 2~3종을 주로 쓰고 가끔 변주한다.
- pop: 기본. 툭 튀어나온다. 대부분의 리액션 자막.
- fade: 조용히 떠오른다. 나레이션, 상황 설명, 정적.
- zoom: 크게 터진다. 그 구간의 핵심 한 방에만.
- typewriter: 한 글자씩. 뜸 들이는 순간, 진지한 설명.
- shake / glitch: 흔들리거나 튄다. 당황, 충격, 사고.
- slide_up / slide_side: 옆이나 아래에서 들어온다. 부가 정보.
- sparkle: 반짝인다. 자화자찬, 예쁜 것, 뿌듯한 순간.
- default: 스타일에 정해진 기본 애니메이션을 쓴다.

# 효과음과 줌
- 효과음은 자막이 뜨는 바로 그 순간에 겹쳐야 산다. 아무 데나 깔지 않는다.
- 줌은 표정이 결정적으로 변하는 순간에 0.8~2.5초로 짧게. 계속 당겨져 있으면 피곤하다.
- 화면효과는 정말 큰 반전에만. 남발하면 촌스러워진다.

# 장면 전환 (transitions)
- 컷 경계 목록을 준다. 그 시각에만 전환을 넣을 수 있다. 다른 시각은 무시된다.
- 모든 컷에 전환을 넣지 마라. 대부분의 컷은 그냥 붙는 게 낫다.
  화제가 바뀌거나, 시간이 건너뛰거나, 장소가 달라질 때만 넣는다.
- dissolve: 부드럽게 겹친다. 시간 경과, 잔잔한 마무리.
- fade_black: 검게 떨어졌다 올라온다. 챕터가 끝날 때.
- flash_white: 하얗게 번쩍. 빠른 화제 전환, 리듬을 끊을 때.
- blur / slide / push / pull: 가볍게 넘긴다.
- glitch / spin: 강하다. 아주 가끔만.
- 길이는 0.3~0.8초. 짧을수록 템포가 산다.

# 지켜야 할 규칙
- 모든 시각은 제공된 타임라인의 초 단위이며, 대사 구간 안이나 그 바로 뒤여야 한다.
- 자막끼리 시간이 겹치면 안 된다. 최소 0.15초는 띄운다.
- 자막 길이는 0.6~2.5초.
- 효과음은 반드시 제공된 목록의 이름만 쓴다. 목록에 없으면 쓰지 않는다.
- 화면효과와 전환도 반드시 제공된 목록의 이름만 쓴다.
- 전환은 제공된 컷 경계 시각에만 넣는다.
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

# 쓸 수 있는 장면 전환
{transition_catalog}

# 컷 경계 (이 시각에만 전환을 넣을 수 있음)
{boundary_list}

# 쓸 수 있는 자막 애니메이션
{anim_list}

# 자막 위치 값
{position_list}
{style_ref_block}
# 대사 스크립트
{context_block}{script}

위 '이번에 작업할 구간' 안에 들어가는 큐만 만들어라. 맥락으로 준 앞부분(<<맥락>> 표시)
에는 큐를 만들지 마라.
"""


def _schema(
    styles: Iterable[str],
    sfx_names: list[str],
    effect_names: list[str],
    anim_names: list[str],
    transition_names: list[str],
    boundaries: list[float],
) -> dict[str, Any]:
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
            "anim": {"type": "string", "enum": ["default", *anim_names],
                     "description": "등장 애니메이션. 고민되면 default"},
        },
        "required": ["start", "end", "text", "style", "position", "anim"],
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

    transition = {
        "type": "object",
        "properties": {
            "time": {"type": "number", "description": "컷 경계 시각. 목록에 있는 값만"},
            "name": {"type": "string", "enum": transition_names},
            "duration": {"type": "number", "description": "전환 길이(초). 0.3~0.8 권장"},
        },
        "required": ["time", "name", "duration"],
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
    if transition_names and boundaries:
        props["transitions"] = {"type": "array", "items": transition}

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


# ---------------------------------------------------------------- 직접 쓴 JSON 읽기

#: 섹션별 필드 규격: (필수 필드, 선택 필드와 기본값, 만들 클래스)
_SECTIONS: dict[str, tuple[dict[str, type], dict[str, Any], Any]] = {
    "captions": ({"start": float, "end": float, "text": str},
                 {"style": "reaction", "position": "default", "anim": "default"}, Caption),
    "sfx":      ({"time": float, "name": str}, {}, SfxCue),
    "zooms":    ({"start": float, "end": float, "scale": float}, {}, ZoomCue),
    "effects":  ({"start": float, "end": float, "name": str}, {}, EffectCue),
    "transitions": ({"time": float, "name": str}, {"duration": 0.5}, TransitionCue),
}

_EXAMPLE = """\
{
  "captions": [
    {"start": 1.0, "end": 2.2, "text": "?!", "style": "reaction", "position": "top"}
  ],
  "sfx":     [{"time": 1.0, "name": "ding"}],
  "zooms":   [{"start": 4.0, "end": 6.0, "scale": 1.3}],
  "effects": [{"start": 4.0, "end": 4.5, "name": "flash"}],
  "transitions": [{"time": 8.0, "name": "dissolve", "duration": 0.5}]
}"""


def _suggest(word: str, options: Iterable[str]) -> str:
    """오타로 보이면 비슷한 이름을 알려준다."""
    import difflib

    match = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return f" — '{match[0]}' 를 쓰려던 게 아닌가요?" if match else ""


def parse(data: Any, *, source: str = "큐시트") -> Cuesheet:
    """사람이 직접 쓴 JSON 을 Cuesheet 로 바꾼다.

    구조가 잘못된 곳을 하나씩 죽지 말고 **전부 모아서** 알려준다.
    한 번 고치고 다시 돌리는 왕복을 줄이는 게 목적이다.
    """
    problems: list[str] = []

    if not isinstance(data, dict):
        raise CuesheetError(
            f"{source}: 최상위가 객체({{ }})여야 합니다. 예시:\n{_EXAMPLE}"
        )

    # 섹션 이름에 오타가 있으면 지적하되, 내용 검사는 이어서 한다.
    # 이름만 고치고 다시 돌렸더니 그제서야 안쪽 오타가 나오는 왕복을 없앤다.
    import difflib

    resolved: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SECTIONS:
            resolved[key] = value
            continue
        near = difflib.get_close_matches(key, list(_SECTIONS), n=1, cutoff=0.6)
        if near and near[0] not in data:
            problems.append(f"모르는 항목 '{key}' — '{near[0]}' 를 쓰려던 게 아닌가요?")
            resolved[near[0]] = value
        else:
            problems.append(f"모르는 항목 '{key}'{_suggest(key, _SECTIONS)}")

    built: dict[str, list] = {name: [] for name in _SECTIONS}

    for section, (required, optional, cls) in _SECTIONS.items():
        items = resolved.get(section, [])
        if items is None:
            continue
        if not isinstance(items, list):
            problems.append(f"'{section}' 은 목록([ ])이어야 합니다")
            continue

        for i, item in enumerate(items):
            where = f"{section}[{i}]"
            if not isinstance(item, dict):
                problems.append(f"{where}: 객체({{ }})여야 합니다")
                continue

            known = set(required) | set(optional)
            for key in item:
                if key not in known:
                    problems.append(f"{where}: 모르는 필드 '{key}'{_suggest(key, known)}")

            values: dict[str, Any] = {}
            missing = [k for k in required if k not in item]
            if missing:
                problems.append(f"{where}: 빠진 필드 {', '.join(missing)}")
                continue

            bad = False
            for key, kind in required.items():
                value = item[key]
                if kind is float:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        problems.append(f"{where}.{key}: 숫자여야 합니다 (받은 값: {value!r})")
                        bad = True
                    else:
                        values[key] = float(value)
                else:
                    if not isinstance(value, str):
                        problems.append(f"{where}.{key}: 문자열이어야 합니다 (받은 값: {value!r})")
                        bad = True
                    else:
                        values[key] = value
            if bad:
                continue

            for key, default in optional.items():
                value = item.get(key, default)
                if isinstance(default, str):
                    if not isinstance(value, str):
                        problems.append(f"{where}.{key}: 문자열이어야 합니다 (받은 값: {value!r})")
                        continue
                    values[key] = value
                else:  # 숫자 기본값을 가진 선택 필드 (전환 길이 등)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        problems.append(f"{where}.{key}: 숫자여야 합니다 (받은 값: {value!r})")
                        continue
                    if value <= 0:
                        problems.append(f"{where}.{key}: 0보다 커야 합니다 ({value})")
                        continue
                    values[key] = float(value)

            start, end = values.get("start"), values.get("end")
            if start is not None and end is not None and end <= start:
                problems.append(
                    f"{where}: 끝({end})이 시작({start})보다 뒤여야 합니다"
                )
                continue
            for key in ("start", "end", "time"):
                if key in values and values[key] < 0:
                    problems.append(f"{where}.{key}: 음수는 안 됩니다 ({values[key]})")
                    bad = True
            if bad:
                continue

            built[section].append(cls(**values))

    if problems:
        listing = "\n".join(f"  - {p}" for p in problems)
        raise CuesheetError(
            f"{source} 에서 {len(problems)}군데가 잘못됐습니다:\n{listing}\n\n"
            f"올바른 형식:\n{_EXAMPLE}"
        )

    return Cuesheet(
        captions=built["captions"], sfx=built["sfx"], zooms=built["zooms"],
        effects=built["effects"], transitions=built["transitions"],
    )


def check_names(
    sheet: Cuesheet,
    *,
    style_names: Iterable[str],
    sfx_names: Iterable[str],
    effect_names: Iterable[str],
    positions: Iterable[str] | None = None,
    anim_names: Iterable[str] | None = None,
    transition_names: Iterable[str] | None = None,
) -> list[str]:
    """이름이 실제로 쓸 수 있는 것인지 본다. 치명적이진 않아 경고로 돌려준다."""
    styles = set(style_names)
    sfx = set(sfx_names)
    effects = set(effect_names)
    places = set(positions or []) | {"default"}
    anims = set(anim_names or []) | {"default"}
    trans = set(transition_names or [])

    warnings: list[str] = []
    for i, cap in enumerate(sheet.captions):
        if cap.style not in styles:
            warnings.append(
                f"captions[{i}]: 모르는 스타일 '{cap.style}'{_suggest(cap.style, styles)} "
                f"— reaction 으로 대체됩니다"
            )
        if cap.position not in places:
            warnings.append(
                f"captions[{i}]: 모르는 위치 '{cap.position}'{_suggest(cap.position, places)} "
                f"— 스타일 기본 위치를 씁니다"
            )
    for i, cap in enumerate(sheet.captions):
        if anim_names is not None and cap.anim not in anims:
            warnings.append(
                f"captions[{i}]: 모르는 애니메이션 '{cap.anim}'{_suggest(cap.anim, anims)} "
                f"— 스타일 기본값을 씁니다"
            )
    for i, cue in enumerate(sheet.transitions):
        if transition_names is not None and cue.name not in trans:
            warnings.append(
                f"transitions[{i}]: 쓸 수 없는 전환 '{cue.name}'{_suggest(cue.name, trans)} "
                f"— 무시됩니다"
            )
    for i, cue in enumerate(sheet.sfx):
        if cue.name not in sfx:
            warnings.append(
                f"sfx[{i}]: 라이브러리에 없는 효과음 '{cue.name}'{_suggest(cue.name, sfx)} "
                f"— 무시됩니다"
            )
    for i, cue in enumerate(sheet.effects):
        if cue.name not in effects:
            warnings.append(
                f"effects[{i}]: 쓸 수 없는 화면효과 '{cue.name}'{_suggest(cue.name, effects)} "
                f"— 무시됩니다"
            )
    return warnings


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
    anim_names: list[str] | None = None,
    transition_names: list[str] | None = None,
    boundaries: list[float] | None = None,
    style_ref: list[str] | None = None,
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
    anim_names = anim_names or []
    transition_names = transition_names or []
    boundaries = boundaries or []
    schema = _schema(
        style_names, sfx_names, effect_names, anim_names, transition_names, boundaries
    )
    lines = _script_lines(utterances)

    sfx_catalog = "\n".join(f"- {n}" for n in sfx_names) or "(없음 — 효과음을 만들지 마라)"
    effect_catalog = "\n".join(f"- {n}" for n in effect_names) or "(없음 — 화면효과를 만들지 마라)"
    transition_catalog = "\n".join(f"- {n}" for n in transition_names) or "(없음 — 전환을 만들지 마라)"
    anim_list = "\n".join(f"- {n}" for n in ["default", *anim_names])
    position_list = "\n".join(
        [f"- default: 스타일 기본 위치"] + [f"- {k}" for k in POSITION_OVERRIDES]
    )
    density = _DENSITY_GUIDE.get(cfg.density, _DENSITY_GUIDE["normal"])

    style_ref_block = ""
    if style_ref:
        samples = "\n".join(f"- {line}" for line in style_ref[: cfg.style_ref_limit])
        style_ref_block = (
            "\n# 이 채널의 자막 예시\n"
            "아래는 실제로 이 채널에서 쓰던 자막들이다. 문구를 베끼지 말고, "
            "길이·말투·호흡·어떤 순간에 자막을 넣는지를 따라라.\n"
            f"{samples}\n"
        )

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
        in_window = [b for b in boundaries if window[0].start <= b <= window[-1].end + 3.0]
        boundary_list = ", ".join(f"{b:.2f}" for b in in_window) or "(이 구간엔 컷 경계 없음)"
        user = _USER_TEMPLATE.format(
            tone=cfg.tone,
            total=total_duration,
            win_start=window[0].start,
            win_end=window[-1].end,
            density=density,
            sfx_catalog=sfx_catalog,
            effect_catalog=effect_catalog,
            transition_catalog=transition_catalog,
            boundary_list=boundary_list,
            anim_list=anim_list,
            position_list=position_list,
            style_ref_block=style_ref_block,
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
        sheet.transitions += [
            TransitionCue(**c) for c in data.get("transitions", []) if lo <= c["time"] <= hi
        ]

    return sanitize(sheet, total_duration)


# ---------------------------------------------------------------- 후처리


def sanitize(sheet: Cuesheet, total: float) -> Cuesheet:
    """범위 초과·길이 이상·중복을 정리한다. 모델 출력을 그대로 믿지 않는다.

    겹치는 항목은 여기서 버리지 않는다. 트랙을 늘려 담는 건 배치 단계(packing)의
    일이고, 한 클립에 둘 다 걸 수 없는 줌 같은 경우만 거기서 걸러진다.
    """
    captions: list[Caption] = []
    seen: set[tuple[str, int]] = set()
    for cap in sorted(sheet.captions, key=lambda c: c.start):
        if not cap.text.strip() or cap.end <= cap.start:
            continue
        cap.start = max(0.0, min(cap.start, total))
        cap.end = min(max(cap.end, cap.start + 0.3), total)
        if cap.end <= cap.start:
            continue
        # 같은 문구가 거의 같은 시각에 두 번 나온 건 청크 경계 중복이다
        key = (cap.text.strip(), int(cap.start * 2))
        if key in seen:
            continue
        seen.add(key)
        captions.append(cap)

    def clamp(items):
        out = []
        for it in sorted(items, key=lambda x: x.start):
            it.start = max(0.0, min(it.start, total))
            it.end = min(max(it.end, it.start + 0.2), total)
            if it.end > it.start:
                out.append(it)
        return out

    sfx: list[SfxCue] = []
    seen_sfx: set[tuple[str, int]] = set()
    for cue in sorted(sheet.sfx, key=lambda x: x.time):
        if not 0.0 <= cue.time <= total:
            continue
        key = (cue.name, int(cue.time * 20))  # 0.05초 안에 같은 소리 두 번은 중복
        if key in seen_sfx:
            continue
        seen_sfx.add(key)
        sfx.append(cue)

    transitions: list[TransitionCue] = []
    seen_at: set[int] = set()
    for cue in sorted(sheet.transitions, key=lambda x: x.time):
        if not 0.0 <= cue.time <= total:
            continue
        key = int(cue.time * 100)  # 한 경계에 전환은 하나만
        if key in seen_at:
            continue
        seen_at.add(key)
        cue.duration = max(0.1, min(cue.duration, 2.0))
        transitions.append(cue)

    return Cuesheet(
        captions=captions, sfx=sfx, zooms=clamp(sheet.zooms),
        effects=clamp(sheet.effects), transitions=transitions,
    )
