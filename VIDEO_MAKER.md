# 🎬 video_maker — 주제 → 대본 → 영상 자동 생성기

주제 문장 하나만 넣으면 **대본 → 나레이션 음성 → 장면 이미지 → 영상(mp4)**
까지 한 번에 만들어 주는 파이썬 CLI입니다.

```
주제 입력  →  ① 대본 생성(LLM)  →  ② 나레이션(TTS)  →  ③ 장면 이미지(Pillow)  →  ④ 합성(ffmpeg)  →  🎬 mp4
```

## 빠른 시작

```bash
# 1) 파이썬 의존성
pip install -r video_maker/requirements.txt

# 2) 시스템 의존성 (Ubuntu 예시)
sudo apt-get install -y ffmpeg fonts-nanum

# 3) 실행
python make.py "커피의 역사"
```

출력물은 `output/<주제>/` 아래에 생성됩니다.
- `script.json` — 생성된 대본
- `<주제>.mp4` — 최종 영상
- `work/` — 장면별 이미지/음성/클립(중간 산출물)

## 사용법

```bash
python make.py "우주 쓰레기 문제"                    # 세로 쇼츠(9:16) 기본
python make.py "환율이 오르는 이유" --orientation horizontal   # 가로 유튜브(16:9)
python make.py "커피의 역사" --no-api               # Claude 없이 템플릿 대본으로
python make.py "블랙홀" --script-only               # 대본만 만들고 영상은 생략
python make.py "김치" -o /path/to/out.mp4           # 출력 경로 지정
```

## 대본 품질: Claude 연동

`ANTHROPIC_API_KEY` 환경변수가 설정되어 있으면 대본을 **Claude**가 작성합니다
(후킹 있는 제목, 5~7개 장면, 자연스러운 구어체 나레이션). 키가 없으면
자동으로 템플릿 기반 대본으로 넘어가 파이프라인은 그대로 동작합니다.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export VIDEO_MAKER_MODEL=claude-opus-4-8   # 선택
python make.py "제주도 여행 코스"
```

## 나레이션(TTS) — 여러 엔진 지원

`--tts` 옵션(또는 환경변수 `VIDEO_MAKER_TTS`)으로 음성 엔진을 고릅니다.
기본값 `auto`는 **가능한 것부터 순서대로 시도**하고, 모두 실패하면
글자 수에 맞춘 **무음 트랙**으로 대체해 영상은 끝까지 생성됩니다.

| 엔진 | 품질 | 필요 조건 | 비고 |
|------|------|-----------|------|
| `openai` | ★★★ | `OPENAI_API_KEY` | 자연스러운 한국어 |
| `elevenlabs` | ★★★ | `ELEVENLABS_API_KEY` | 다국어 뉴럴 |
| `edge` | ★★★ | 없음(무료), 네트워크 | MS 뉴럴 음성, **추천 기본** |
| `gtts` | ★★ | 없음(무료), 네트워크 | 구글 TTS |
| `espeak` | ★ | `espeak-ng` 설치 | **오프라인**, 로봇 톤이지만 확실 |

auto 시도 순서: `openai → elevenlabs → edge → gtts → espeak → (무음)`

```bash
python make.py "커피의 역사" --tts edge        # 무료 고품질(네트워크 필요)
python make.py "커피의 역사" --tts espeak      # 오프라인
OPENAI_API_KEY=sk-... python make.py "커피의 역사" --tts openai
```

### 엔진별 환경변수 (선택)

```bash
# OpenAI
export OPENAI_TTS_MODEL=gpt-4o-mini-tts      # 기본
export OPENAI_TTS_VOICE=alloy                # alloy/nova/shimmer ...
# ElevenLabs
export ELEVENLABS_VOICE_ID=...               # 음성 ID
export ELEVENLABS_MODEL=eleven_multilingual_v2
# edge-tts
export EDGE_TTS_VOICE=ko-KR-SunHiNeural      # ko-KR-InJoonNeural(남성) 등
# espeak-ng (오프라인)
export ESPEAK_NG_BIN=/path/to/espeak-ng      # PATH에 없을 때
export ESPEAK_NG_SPEED=150                   # 말 속도
```

> 사내/샌드박스 프록시가 커스텀 CA를 쓰는 경우 edge-tts가 인증서 오류를 낼 수
> 있는데, `SSL_CERT_FILE`(또는 `REQUESTS_CA_BUNDLE`)에 CA 번들 경로가 있으면
> 자동으로 신뢰하도록 처리돼 있습니다.

## 구조

```
make.py                  CLI 진입점 (오케스트레이션)
video_maker/
  config.py              해상도/폰트/색상 등 설정
  script_gen.py          주제 → 대본 (Claude 또는 템플릿)
  tts_engines.py         TTS 백엔드 모음 (openai/elevenlabs/edge/gtts/espeak)
  narration.py           엔진 선택 + 자동 폴백, 실패 시 무음 대체
  visuals.py             장면 → 타이틀 카드 PNG (Pillow)
  video.py               이미지+음성 → 클립 → 이어붙이기 (ffmpeg)
```

## 커스터마이징 아이디어

- `visuals.py`의 색상/폰트/레이아웃을 바꿔 브랜드 톤 적용
- 무료 이미지 API(예: Pexels)나 이미지 생성 모델을 붙여 배경을 실사/일러스트로
- 배경음악 트랙 추가(`video.py`에서 오디오 믹싱)
- 다른 TTS(예: ElevenLabs, OpenAI TTS)로 음성 품질 향상
