---
name: explainer-video
description: 대본(또는 주제)으로 보이스오버 + 애니메이션 설명 영상(16:9 MP4)을 만든다. Gemini TTS로 음성 → Whisper로 단어 타이밍 → Claude가 장면 기획(scenes.json) → Remotion 렌더. "영상 만들어줘", "이 대본으로 영상", "설명 영상", "~ 주제로 영상", "장면 기획해줘", "영상 수정해줘(색/장면/말투/속도)" 같은 요청에 사용.
---

# 설명 영상 만들기 (대본 → MP4)

프로젝트 루트: 이 스킬이 있는 저장소 루트 (`pipeline/`, `video/`, `work/` 가 있는 곳).
Windows PowerShell 기준. Python은 항상 `.\.venv\Scripts\python.exe`, 실행 전에 `$env:PYTHONIOENCODING="utf-8"`.

## 0. 사전 확인 (처음 한 번)
- `.venv`, `video/node_modules` 가 없으면 README의 설치 절차를 먼저 진행한다.
- `GEMINI_API_KEY` 는 사용자 환경변수에 있어야 한다 (스크립트가 레지스트리에서도 읽으므로 앱 재시작은 불필요). **키 값을 출력하거나 파일에 쓰지 않는다.**

## 1. 작업 폴더와 대본
- 작업 이름(job)은 영문 소문자-하이픈 (예: `claude-code-intro`). 폴더: `work/<job>/`
- 사용자가 **대본을 줬으면** 그대로 `work/<job>/script.txt` 에 저장 (UTF-8).
- **주제만 줬으면** 대본을 직접 쓰고, 사용자에게 보여 준 뒤 OK를 받고 진행한다 (사용자가 "알아서 끝까지"라고 했으면 생략).
- 대본 작성 규칙:
  - 말하듯 쓰는 구어체 한국어. **한 줄에 한 문장**, 한 문장은 대략 40자 이내.
  - 첫 문장은 시청자의 고민·질문으로 시작 (훅), 마지막은 한 줄 요약.
  - 영어 고유명사·약어는 대본에 **영어 그대로** 쓰고, TTS가 잘못 읽으면 발음 사전으로 교정한다:
    `표기 = 발음` 한 줄씩. 루트 `pronounce.txt` = 공개 · 일반 용어만, 루트 `pronounce.local.txt` = **회사·대외비 용어(git 제외)**,
    `work/<job>/pronounce.txt` = 작업별. 회사 제품명·내부 시스템명·고객사명은 절대 `pronounce.txt` 에 넣지 않는다.
    한 번만 바꿀 땐 인라인 `{M/M으로|맨 먼스로}`. 자막·`words[].text`·`at` 은 **표기** 기준이다.
  - 빈 줄로 단락을 나눈다 (주제 전환 지점). TTS가 단락 단위로 나눠 합성해 톤 드리프트를 막는다.
  - 분량 기준: 1분 ≈ 13~15문장.

## 2. 음성 + 타이밍
```powershell
.\.venv\Scripts\python.exe pipeline\run.py <job> [--style "말투 지시"] [--voice Kore]
```
- `scenes.json` 이 없으면 TTS → Whisper → `timing.json` 까지 만들고 멈춘다.
- 출력의 **"경고:"** 줄을 반드시 확인한다. `--strict` 로 돌기 때문에 경고가 있으면 실패한다.
  - "대본 시작 전에 다른 말" → TTS가 말투 지시를 읽음. `--style` 을 짧게 바꿔 다시.
  - "일치율 낮음" → TTS가 문장을 빠뜨림. 그대로 다시 돌리거나 대본을 짧게 나눈다.
  - "중간에 대본에 없는 말" → TTS가 말을 지어냄. run.py 가 해당 조각만 자동 재합성(최대 3회). 그래도 실패하면 그 단락을 쪼갠다.
    `--style` 에 "강의", "설명" 같은 단어가 있으면 TTS가 내용을 덧붙이는 경향이 있다 → "차분한 사내 브리핑 톤" 처럼 **톤만** 지시한다.
- **"목소리 높낮이(Hz)"** 줄을 본다: `*` 조각은 다른 목소리로 나와 자동 재합성된다. 끝까지 경고면 `--style` 을 빼고 다시.
- **"발음 점검"** 목록을 본다: 대본과 다르게 들린 어절. 숫자 표기 차이(다섯 → 5)·연음(1안 → 이란)은 무시하고,
  진짜 오독(예: M/M → 엠퍼엠)만 `pronounce.txt` 에 추가한 뒤 `--from tts` 로 다시 (바뀐 조각만 재합성).
- 말투 기본값: 밝고 신뢰감 있는 테크 유튜버 톤. 사용자가 원하면 `--style` 로 변경.
- 목소리: Kore(기본, 여성 차분), Puck/Charon/Fenrir/Orus 등 30종. 사용자가 원하면 `--voice`.

## 3. 장면 기획 (`work/<job>/scenes.json`) ← Claude의 핵심 작업
1. `video/SCENES.md` 를 읽는다 (장면 종류, props, TimeRef 규칙).
2. `work/<job>/timing.json` 의 `sentences` 를 읽고 흐름을 파악한다.
3. 기획 원칙:
   - 장면 1개 = 보통 1~3문장, **10초 넘게 같은 화면 금지**.
   - 화면 글자는 자막 반복이 아니라 **핵심 키워드 요약** (자막은 하단에 자동으로 나온다).
   - 같은 type 연속 사용 피하기. 흐름/순서 설명엔 `steps`, 코드·명령어엔 `terminal`, 나열엔 `bullets`.
   - 요소 등장 타이밍은 **그 단어를 말하는 순간**에 맞춘다: `"at": "위스퍼"` 처럼 어절 일부를 쓴다.
     (어절은 `timing.json` 의 `words[].text` 에 실제로 있는 글자여야 한다.)
   - 강조는 `*별표*`.
4. 사용자가 색 테마를 원하면 `theme` 로 덮어쓴다.

## 4. 미리보기 확인 (렌더 전에 반드시)
```powershell
.\.venv\Scripts\python.exe pipeline\run.py <job> --from render --stills
```
- `work/<job>/stills/scene*.png` 를 **Read로 전부 열어 본다** (많으면 ffmpeg `tile` 로 묶어서). steps/bullets 는 `_end` 컷(전부 켜진 상태)도 있다. 확인할 것:
  글자 넘침/줄바꿈 깨짐, 카드 정렬, 빈 화면, 자막과 겹침, 한글 폰트(네모 깨짐).
- 문제 있으면 `scenes.json` (또는 `video/src/scenes.tsx`) 을 고치고 **반드시 다시 stills** (stills 없이 바로 렌더 금지).
- run.py 가 "at '…' 를 그 장면 안의 어절에서 찾지 못함" 경고를 내면 렌더가 멈춘다 → at 을 실제 어절(표기 기준)로 고친다.

## 5. 렌더링과 전달
```powershell
.\.venv\Scripts\python.exe pipeline\run.py <job> --from render
```
- 완료 후 검증: `ffprobe -v error -show_entries stream=codec_type,width,height -show_entries format=duration -of compact work\<job>\output.mp4`
  → 1920×1080, video+audio 스트림, 길이 ≈ 음성 길이 + 0.8초. 30MB 넘으면 원격(폰)으로 못 보내니 `--crf 26` 정도로 다시.
- `SendUserFile` 로 `work/<job>/output.mp4` 를 보낸다 (display: render).

## 수정 요청 대응표
| 요청 | 할 일 | 다시 실행 |
|---|---|---|
| 장면 구성/문구/타이밍/색 | `scenes.json` 수정 | `--from render` |
| 말투/목소리/속도 | `--style`/`--voice` 변경 | 전체 (`--from tts`) |
| 대본 내용 | `script.txt` 수정 | 전체 |
| 새로운 장면 디자인 필요 | `video/src/scenes.tsx` 에 컴포넌트 추가 → `SCENES` 등록 → `SCENES.md` 표에 추가 | `--stills` 후 `--from render` |

## 실시간 미리보기 (선택)
`.claude/launch.json` 의 `remotion-studio` 를 `preview_start` 로 띄우면 브라우저 패널에서 타임라인을 돌려 볼 수 있다.
먼저 `--stills` 를 한 번 돌려 `video/public/<job>/` 에 파일이 복사돼 있어야 하고, Studio 오른쪽 props 에서 `job` 을 바꾼다.
