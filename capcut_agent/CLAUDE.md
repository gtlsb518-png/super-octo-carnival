# CapCut Agent — 작업 메모

## 자막 인식 방식 (고정 — 절대 바꾸지 말 것)

자막 인식은 **"영상 오디오를 한 번에 인식 → 단어를 실제 발화 시각으로 각 클립에 분배"** 방식만 쓴다.
사용자가 명시적으로 이 방식으로 고정해달라고 요청함 (2026-08).

- 구현: `transcribe_all_clips` → `load_audio_16k`(오디오 한 번만 로드) + `_transcribe_array`(전체 1회 인식) + 단어를 클립에 **겹침(overlap) 기준**으로 분배 + 소리 있는데 놓친 클립만 선별 재인식.
- **클립을 하나씩 인식(per-clip)하거나 병렬(ThreadPool)로 돌리는 방식으로 되돌리지 말 것** — GPU에서 느리고, 사용자가 싫어함.
- 단어→클립 배정은 **중간점(midpoint)이 아니라 겹침 기준**으로 한다. 중간점으로 하면 클립 첫 단어가 잘려나간 무음 구간에 걸려 통째로 사라진다(실제 버그였음).
- 오디오 로드 실패 시에만 per-clip ffmpeg 방식으로 자동 폴백.

## 크래시 방지 (절대 되돌리지 말 것)

- 모델 로드는 **GPU(float16) 우선 → 실패 시 CPU `compute_type="auto"`**.
- CPU에서 `compute_type="int8"`을 강제하면 그 명령을 지원 안 하는 CPU에서 프로그램이 통째로 꺼진다(Illegal instruction, 파이썬 try/except로 못 잡음).

## 자막 후처리 정책

- 자막 후처리(중복 합치기·끝음절 흡수 등)는 **넣지 않는다** — 사용자가 원치 않음. `subtitle_chunks_for_timeline`은 인식 결과를 그대로 반환.
- 숫자/영어 잡음 필터(`clean_recognized_text`, `_is_number_babble`, `is_hallucinated_line` 등)는 유지 — `I`, `it`, `2 2 3` 같은 헛것 제거에 필요.

## 테스트

- `python3 tests/자막_기준_테스트.py` — 자막 끊기 gold 테스트. 변경 후 반드시 통과 확인.
- 이 환경(샌드박스)엔 **ffmpeg·Whisper 모델·GPU 가 없어** 실제 인식은 못 돌린다. 순수 파이썬 로직(분배·필터·청킹)만 여기서 검증 가능.
