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

## 나레이션(TTS)

- 기본은 **gTTS**(구글 TTS, 한국어)로 음성을 만듭니다. **네트워크가 필요**합니다.
- TTS에 실패하면(오프라인 등) 글자 수에 맞춘 **무음 트랙**으로 대체해
  영상은 끝까지 생성됩니다. 자막은 화면에 그대로 표시됩니다.

## 구조

```
make.py                  CLI 진입점 (오케스트레이션)
video_maker/
  config.py              해상도/폰트/색상 등 설정
  script_gen.py          주제 → 대본 (Claude 또는 템플릿)
  narration.py           문장 → 음성(mp3), 실패 시 무음 대체
  visuals.py             장면 → 타이틀 카드 PNG (Pillow)
  video.py               이미지+음성 → 클립 → 이어붙이기 (ffmpeg)
```

## 커스터마이징 아이디어

- `visuals.py`의 색상/폰트/레이아웃을 바꿔 브랜드 톤 적용
- 무료 이미지 API(예: Pexels)나 이미지 생성 모델을 붙여 배경을 실사/일러스트로
- 배경음악 트랙 추가(`video.py`에서 오디오 믹싱)
- 다른 TTS(예: ElevenLabs, OpenAI TTS)로 음성 품질 향상
