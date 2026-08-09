# yeneung — 컷편집된 영상을 캡컷 예능 프로젝트로

> **이 폴더는 독립 프로젝트입니다.** 매매봇 저장소와 아무 관련이 없고,
> 전달 목적으로만 여기에 들어 있습니다. 별도 저장소로 옮기려면 깃허브에서
> 빈 저장소를 하나 만든 뒤:
>
> ```bash
> cd yeneung-capcut
> git init && git add -A && git commit -m "최초 커밋"
> git remote add origin https://github.com/<계정>/yeneung-capcut.git
> git push -u origin main
> ```
>
> 그 다음 매매봇 저장소에서 이 폴더를 지우면 됩니다.


컷편집만 끝난 영상을 넣으면, **캡컷에서 바로 열리는 프로젝트**를 만들어 줍니다.
자막·효과음·줌·화면효과가 이미 타임라인에 배치된 상태로 열립니다.

렌더링된 완성본이 아니라 **편집 가능한 초안**입니다. 마음에 안 드는 자막은
캡컷에서 그냥 고치면 됩니다.

```
영상.mp4
   │
   ├─ 무음 구간 감지        → 늘어지는 부분 자동 컷
   ├─ Whisper 받아쓰기      → 대사 자막(SRT)
   ├─ Claude 큐시트 생성    → 예능 자막 / 효과음 / 줌 / 화면효과
   └─ 캡컷 초안 생성        → 캡컷에서 열기
```

## 만들어지는 것

| 트랙 | 내용 |
|---|---|
| 영상 | 무음 구간이 잘린 컷 클립들. 줌은 키프레임으로 들어감 |
| 예능 자막 | `?!`, `당황`, `이게 되네` 같은 제작진 시점 리액션 자막 |
| 대사 자막 | 받아쓴 대사. 하단 기본 자막 |
| 효과음 | 자막 타이밍에 맞춘 효과음 |
| 화면효과 | 플래시, 흔들림, 글리치 등 |

## 요구 사항

- **윈도우 + 캡컷 데스크톱** (초안 생성은 어디서든 되지만, 열고 내보내는 건 윈도우 캡컷)
- Python 3.11 이상
- Anthropic API 키 (`ANTHROPIC_API_KEY` 환경변수)
- ffmpeg는 **필요 없습니다**

## 설치

```bash
git clone <이 저장소>
cd yeneung-capcut
pip install -r requirements.txt
```

환경 점검:

```bash
python -m yeneung doctor
```

캡컷 초안 폴더를 못 찾는다고 나오면 `--draft-dir` 로 직접 알려주면 됩니다.
보통 여기 있습니다:

```
%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft
```

## 효과음

**따로 준비할 필요 없습니다.** 처음 실행하면 기본 효과음 12종이 자동으로
생성됩니다. 어디서 받아오는 게 아니라 **합성해서 만드는 것**이라 저작권 문제가
없고 네트워크도 필요 없습니다.

띵(`ding`) · 딩동댕(`correct`) · 삐-(`error`) · 뿅(`boing`) · 뾱(`pop`) ·
슝(`swoosh`) · 둥(`drum`) · 두구두구(`tension`) · 뿌우-(`fail`) ·
반짝(`sparkle`) · 귀뚜라미(`crickets`) · 찌익(`record_scratch`)

```bash
python -m yeneung sfx            # 목록 확인
python -m yeneung sfx generate   # 직접 만들기 (자동 생성이 안 됐을 때)
```

**소리를 바꾸려면** 같은 이름의 파일로 덮어쓰면 됩니다. 이미 있는 파일은
다시 만들지 않습니다.

```
assets/sfx/ding.wav   ← 원하는 파일로 덮어쓰기
```

**새 효과음을 추가하려면** 파일을 넣고 `assets/sfx/manifest.json` 에 항목을
추가하세요. `desc` 는 Claude 가 **언제 이 소리를 쓸지** 판단하는 유일한
근거이니 쓰임새까지 적어주세요.

```json
{ "laugh": { "file": "laugh.wav", "desc": "방청객 웃음소리. 확실한 웃음 포인트" } }
```

> 방청객 웃음소리는 합성으로 흉내 낼 수 없어 기본 팩에 없습니다. 예능에서
> 꽤 중요한 소리라, 필요하면 직접 구해서 위처럼 추가하세요.

## 사용

```bash
python -m yeneung run 영상.mp4
```

캡컷을 열면 `영상_예능` 프로젝트가 보입니다. **목록에 안 보이면** 아무
프로젝트나 열었다 나오거나 캡컷을 재시작하면 갱신됩니다.

### 자주 쓰는 옵션

```bash
# 자막·효과를 촘촘하게
python -m yeneung run 영상.mp4 --density high

# 프로그램 톤 바꾸기 — 자막 문체가 실제로 달라집니다
python -m yeneung run 영상.mp4 --tone "차분한 다큐 예능"

# 초안은 안 만들고 큐시트만 확인
python -m yeneung run 영상.mp4 --dry-run

# 일부 기능 끄기
python -m yeneung run 영상.mp4 --no-cut --no-effects

# 빠르게 테스트 (정확도는 떨어짐)
python -m yeneung run 영상.mp4 --whisper-model small
```

### 캐시

받아쓰기와 큐시트는 영상 옆 `.yeneung_<파일명>/` 에 저장됩니다.
다시 돌리면 재사용하므로 빠르고 돈이 안 듭니다.

```bash
python -m yeneung run 영상.mp4 --refresh-cuesheet   # 큐시트만 새로
python -m yeneung run 영상.mp4 --refresh-transcript # 받아쓰기부터 새로
```

`cuesheet.json` 을 직접 열어 자막 문구를 고친 뒤 다시 돌려도 됩니다.

## 설정

`config.example.toml` 을 `config.toml` 로 복사해서 쓰세요.

```bash
python -m yeneung run 영상.mp4 -c config.toml
```

자주 건드리게 되는 값:

| 값 | 뜻 |
|---|---|
| `cut.min_silence` | 이 길이 이상 무음만 자름. 낮추면 템포가 빨라짐 |
| `cut.threshold_db` | 잡음 많은 영상은 `-30` 정도로 올리세요 |
| `cut.padding` | 말꼬리가 잘리면 늘리세요 |
| `cuesheet.tone` | 프로그램 톤. 자막 문체를 가장 크게 바꿉니다 |
| `styles.*` | 자막 크기·색·위치·등장 애니메이션 |

`position_y` 는 **양수가 위쪽**입니다. 자막이 반대로 가면 부호를 뒤집으세요.

## 자막 스타일

| 스타일 | 쓰임 |
|---|---|
| `reaction` | 감탄·놀람. 가장 자주 쓰임 |
| `emphasis` | 그 구간의 핵심 한 방 |
| `narration` | 제작진의 담백한 상황 설명 |
| `whisper` | 속마음, 작은 목소리 |
| `situation` | 장소·시간 안내 CG |

## 알아두어야 할 점

**캡컷 초안 포맷은 비공식입니다.** 캡컷이 업데이트되면서 프로젝트 파일 구조를
바꾸면 생성된 초안이 안 열릴 수 있습니다. 실제로 최근 캡컷 버전들은 초안 파일을
암호화하기 시작했습니다. 이 도구는 새 초안을 **쓰기만** 하므로 지금은 동작하지만,
앞으로도 계속 동작한다고 보장할 수는 없습니다.

안 열릴 경우 대비책: `.yeneung_<파일명>/dialogue.srt` 는 캡컷이 공식 지원하는
형식이라 언제든 직접 가져올 수 있고, `cuesheet.json` 에 모든 자막과 타임코드가
들어 있어 수동 작업의 설계도로 쓸 수 있습니다.

**한글 폰트.** 자막은 캡컷 기본 폰트로 만들어집니다. 캡컷에서 자막 하나를 골라
폰트를 바꾼 뒤 "모두 적용"을 누르면 한 번에 바뀝니다.

**무음 컷은 소리만 봅니다.** 말없이 웃긴 리액션이 있으면 잘려나갈 수 있습니다.
그런 영상은 `cut.min_silence` 를 올리거나 `--no-cut` 을 쓰세요.

**받아쓰기는 완벽하지 않습니다.** 고유명사와 유행어는 특히 틀립니다.
`.yeneung_*/transcript.json` 을 고친 뒤 `--refresh-cuesheet` 로 다시 돌리면
고친 내용이 반영됩니다.

## 문제 해결

| 증상 | 해결 |
|---|---|
| 캡컷 프로젝트 목록에 없음 | 다른 프로젝트를 열었다 나오거나 캡컷 재시작 |
| 초안 폴더를 못 찾음 | `--draft-dir` 로 직접 지정 |
| 자막이 화면 밖 | `styles.*.position_y` 부호를 뒤집으세요 |
| 컷이 너무 과함 | `cut.min_silence` 를 올리거나 `--no-cut` |
| 자막이 너무 많음 | `--density low` |
| 자막 문체가 안 맞음 | `--tone` 을 구체적으로 |
| 효과음이 안 들어감 | `python -m yeneung sfx` 로 라이브러리 확인 |

## 테스트

```bash
pip install pytest imageio-ffmpeg
python -m pytest tests/ -q
```

통합 테스트는 실제 캡컷 초안을 만들어 구조를 검사합니다.

## 구조

```
yeneung/
  media.py       영상 정보 읽기, 오디오 디코딩
  cuts.py        무음 감지, 컷 계산, 타임라인 재매핑
  transcribe.py  faster-whisper 받아쓰기, SRT 출력
  cuesheet.py    Claude 로 예능 큐시트 생성
  styles.py      논리 이름 → 캡컷 내부 리소스 매핑
  sfxgen.py      효과음 합성 (numpy 로 직접 생성)
  sfxlib.py      효과음 라이브러리
  draft.py       pycapcut 으로 초안 굽기
  pipeline.py    전체 흐름
  cli.py         명령줄
```

캡컷 프로젝트 생성은 [pycapcut](https://github.com/GuanYixuan/pyCapCut) 을 씁니다.

## 라이선스

MIT. 캡컷은 ByteDance 의 제품이며 이 프로젝트와 무관합니다.
