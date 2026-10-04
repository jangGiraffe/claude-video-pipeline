"""대본 → MP4 전체 파이프라인.

  python pipeline/run.py <작업이름> [--style "..."] [--voice Kore] [--from tts|transcribe|render] [--stills]
                                    [--no-paid] [--model ...]

음성은 무료 모델(gemini-3.8-flash-tts)로 먼저 만들고, 응답이 없거나 검증(목소리 높낮이·대본 일치)을
끝내 통과하지 못하면 유료 모델(gemini-2.5-pro-preview-tts)로 전체를 다시 만든다.

작업 폴더 work/<작업이름>/ 에 필요한 것:
  script.txt   대본 (필수)
  scenes.json  장면 기획서 (없으면 timing까지 만들고 멈춤 → Claude에게 기획 요청)

만들어지는 것:
  voice.wav, timing.json, subtitles.srt, output.mp4
"""
import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import load_settings, setup_ffmpeg_path
from voice_design import resolve_voice

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
STEPS = ["tts", "transcribe", "render"]
ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}  # 자식 프로세스 출력이 바로바로 보이게
RETRIES = 5       # 유료 모델: 음성 검증 실패 시 문제 조각만 다시 합성하는 최대 횟수
FREE_RETRIES = 3  # 무료 모델: 이만큼 해도 안 되면 유료 모델로 넘어간다
FREE_MODEL, PAID_MODEL = "gemini-3.8-flash-tts", "gemini-2.5-pro-preview-tts"  # tts.py 와 같게 유지
EXIT_DAILY_QUOTA = 3  # tts.py 가 하루 요청 한도 초과로 끝날 때의 종료 코드


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("▶", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, shell=(os.name == "nt" and cmd[0] == "npx"), env=ENV)
    if r.returncode != 0:
        sys.exit(f"실패: {cmd[0]} (exit {r.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--style", default="밝고 친근하지만 신뢰감 있는 테크 유튜버 내레이션. 적당히 빠른 템포.")
    ap.add_argument("--voice", default=None,
                    help="목소리: Kore 등 기본 목소리 또는 voice_design.py 로 만든 별칭 (기본: settings.local.json 의 voice, 없으면 Kore)")
    ap.add_argument("--model", default=None, help="TTS 모델을 하나로 고정 (기본: 무료 → 실패 시 유료). --style 은 3.8 계열에서만 적용")
    ap.add_argument("--no-paid", action="store_true", help="유료 모델로 넘어가지 않음 (무료로만 시도)")
    ap.add_argument("--from", dest="start", choices=STEPS, default="tts", help="이 단계부터 다시 실행")
    ap.add_argument("--stills", action="store_true", help="MP4 대신 장면별 미리보기 PNG만 (work/<job>/stills/)")
    ap.add_argument("--crf", type=int, default=23, help="화질(낮을수록 고화질·큰 파일). 기본 23 ≈ 3분에 20MB 안팎")
    a = ap.parse_args()
    settings = load_settings()
    a.voice = a.voice or settings.get("voice") or "Kore"
    _, designed_model = resolve_voice(a.voice)  # 디자인한 목소리는 만든 모델에서만 쓸 수 있다

    setup_ffmpeg_path()
    work = ROOT / "work" / a.job
    script = work / "script.txt"
    if not script.exists():
        sys.exit(f"{script} 이(가) 없습니다.")
    todo = STEPS[STEPS.index(a.start):]

    stt = [PY, str(ROOT / "pipeline/transcribe.py"), str(work / "voice.wav"), str(work), "--script", str(script), "--strict"]

    quota_hit: list[str] = []  # 하루 요청 한도에 걸린 모델

    def sh(cmd: list[str]) -> bool:
        print("▶", " ".join(cmd))
        code = subprocess.run(cmd, env=ENV).returncode
        if code == EXIT_DAILY_QUOTA and "--model" in cmd:
            quota_hit.append(cmd[cmd.index("--model") + 1])
        return code == 0

    def make_voice(model: str, paid: bool, sole: bool = False) -> bool:
        """한 모델로 음성 생성 → 음성 검증. 검증 실패 시 문제 조각만 다시 합성. 끝내 안 되면 False."""
        tts = [PY, str(ROOT / "pipeline/tts.py"), str(script), str(work / "voice.wav"), "--voice", a.voice,
               "--style", a.style, "--model", model, "--strict",
               "--attempts", "5" if paid or sole else "2"]  # 무료는 응답이 없으면 빨리 포기하고 유료로
        retries = RETRIES if paid or sole else FREE_RETRIES
        if not sh(tts):
            return False
        for attempt in range(retries + 1):
            if sh(stt):
                return True
            bad = work / "bad_spans.json"
            if attempt == retries or not bad.exists():
                return False
            info = json.loads(bad.read_text(encoding="utf-8"))
            chunks = json.loads((work / "tts_chunks.json").read_text(encoding="utf-8"))
            redo = chunks if info["whole"] or not info["spans"] else [
                c for c in chunks if any(s < c["end"] + 0.3 and e > c["start"] - 0.3 for s, e in info["spans"])]
            for c in redo:
                (work / "tts_cache" / c["cache"]).unlink(missing_ok=True)
            print(f"\n재시도 {attempt + 1}/{retries}: TTS 조각 {len(redo)}개 다시 합성 → "
                  + " / ".join(c["text"].splitlines()[0][:20] for c in redo))
            if not sh(tts):
                return False
        return False

    if "tts" in todo:
        # 무료 모델로 먼저 시도 → 안 되면 유료 모델로 '전체' 재합성.
        # (조각마다 모델을 섞으면 모델별 목소리 높이가 달라 또 목소리가 바뀌어 들린다)
        if designed_model:
            if a.model and a.model != designed_model:
                sys.exit(f"목소리 '{a.voice}' 는 {designed_model} 로 만든 것이라 --model {a.model} 와 함께 쓸 수 없습니다.")
            tiers = [(designed_model, designed_model == PAID_MODEL)]  # 다른 모델로 넘어가면 목소리가 달라지므로 대체 없음
        elif a.model:
            tiers = [(a.model, a.model == PAID_MODEL)]
        else:
            tiers = [(FREE_MODEL, False)] + ([] if a.no_paid else [(PAID_MODEL, True)])
        for i, (model, paid) in enumerate(tiers):
            print(f"\n=== 음성 생성: {model} ({'유료' if paid else '무료'}) ===")
            if make_voice(model, paid, sole=len(tiers) == 1):
                print(f"음성 완료: {model} ({'유료' if paid else '무료'})")
                break
            if i + 1 < len(tiers):
                print(f"\n{model} 로는 실패 → {tiers[i + 1][0]} ({'유료' if tiers[i + 1][1] else '무료'}) 로 전체 다시 합성")
        else:
            if quota_hit:
                sys.exit(f"실패: 하루 요청 한도 초과 ({', '.join(dict.fromkeys(quota_hit))}). "
                         "위 tts 출력의 재시도 안내 시간 뒤에 다시 실행하거나 "
                         "--model 로 한도에 여유 있는 모델을 지정하세요.")
            sys.exit("실패: 음성 생성/검증" +("" if len(tiers) > 1 or a.model else " (--no-paid 라 유료 모델은 쓰지 않음)"))
    elif "transcribe" in todo:
        if not sh(stt):
            sys.exit("실패: 음성 검증 (위 경고 참고)")

    if not (work / "scenes.json").exists():
        print(f"\n타이밍 준비 완료. 이제 {work / 'scenes.json'} 장면 기획서가 필요합니다.")
        print(f"Claude에게: \"work/{a.job} 영상 장면 기획해줘\" 라고 요청한 뒤 --from render 로 다시 실행하세요.")
        return

    pub = ROOT / "video" / "public" / a.job
    pub.mkdir(parents=True, exist_ok=True)
    for f in ["voice.wav", "timing.json", "scenes.json"]:
        shutil.copy2(work / f, pub / f)
    # image 장면용 이미지: work/<job>/images/ → public/<job>/images/
    shutil.rmtree(pub / "images", ignore_errors=True)
    if (work / "images").is_dir():
        shutil.copytree(work / "images", pub / "images")
    missing = [s["props"]["src"] for s in json.loads((work / "scenes.json").read_text(encoding="utf-8"))["scenes"]
               if s["type"] == "image" and not (work / "images" / s["props"]["src"]).exists()]
    if missing:
        sys.exit(f"이미지 파일이 없습니다 (work/{a.job}/images/): {', '.join(missing)}")
    props = f"--props={{\"job\":\"{a.job}\"}}"

    timing = json.loads((work / "timing.json").read_text(encoding="utf-8"))
    scenes = json.loads((work / "scenes.json").read_text(encoding="utf-8"))["scenes"]
    starts = [0.0] + [resolve_sec(timing, s["at"]) for s in scenes[1:]]
    ends = starts[1:] + [timing["duration"]]
    if check_refs(timing, scenes, starts) and not a.stills:
        sys.exit("scenes.json 의 at 을 고친 뒤 다시 실행하세요.")

    if a.stills:
        # 각 장면 70% 지점 PNG. 요소가 차례로 켜지는 장면(steps/bullets)은 전부 켜진 끝 직전도 한 장 더
        out = work / "stills"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        for i, (s, e) in enumerate(zip(starts, ends)):
            shots = [("", s + (e - s) * 0.7)]
            if scenes[i]["type"] in ("steps", "bullets"):
                shots.append(("_end", e - 0.3))
            for suffix, t in shots:
                run(["npx", "remotion", "still", "src/index.ts", "Explainer",
                     str(out / f"scene{i}_{scenes[i]['type']}{suffix}.png"), f"--frame={int(t * 30)}", props, "--log=error"],
                    cwd=ROOT / "video")
        print(f"\n장면별 미리보기: {out}")
        return

    run(["npx", "remotion", "render", "src/index.ts", "Explainer", str(work / "output.mp4"), props, f"--crf={a.crf}", "--log=error"],
        cwd=ROOT / "video")
    print(f"\n완성: {work / 'output.mp4'}")

    # 완성본을 환경별 보관 폴더로 복사 (settings.local.json 의 output_dir, 없으면 생략)
    # 파일 이름 앞에 생성일(렌더한 날)을 붙인다: 20261004_<job>.mp4
    dest = settings.get("output_dir")
    if dest:
        dest = Path(os.path.expandvars(os.path.expanduser(dest)))
        # output_subdirs: {"ojt-*": "OJT"} 처럼 작업 이름 패턴별 하위 폴더 (먼저 맞는 것 사용)
        for pat, sub in settings.get("output_subdirs", {}).items():
            if fnmatch.fnmatch(a.job, pat):
                dest = dest / sub
                break
        dest.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.date.today():%Y%m%d}_{a.job}.mp4"
        shutil.copy2(work / "output.mp4", dest / name)
        print(f"복사: {dest / name}")


def check_refs(timing: dict, scenes: list[dict], starts: list[float]) -> bool:
    """문자열 at 이 해당 장면 안에서 실제로 발음되는지 확인. 못 찾으면 장면 시작으로 조용히 밀리므로 경고."""
    bad = []

    def walk(v, i):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "at" and isinstance(x, str):
                    t = resolve_sec(timing, x, starts[i], strict=True)
                    end = starts[i + 1] if i + 1 < len(starts) else timing["duration"]
                    if t is None or t >= end:
                        bad.append((i, x))
                else:
                    walk(x, i)
        elif isinstance(v, list):
            for x in v:
                walk(x, i)

    for i, s in enumerate(scenes):
        if isinstance(s["at"], str) and i > 0 and resolve_sec(timing, s["at"], strict=True) is None:
            bad.append((i, s["at"]))
        walk(s["props"], i)
    for i, x in bad:
        print(f"경고: scene{i} 의 at '{x}' 를 그 장면 안의 어절에서 찾지 못함")
    return bool(bad)


def resolve_sec(timing: dict, ref, after: float = 0.0, strict: bool = False) -> float | None:
    """video/src/timing.tsx 의 resolveSec 과 같은 규칙 (숫자=문장 시작, 문자열=어절 검색)."""
    if isinstance(ref, (int, float)):
        return timing["sentences"][int(ref)]["start"]
    norm = lambda s: re.sub(r"[\s\W_]+", "", s).lower()
    q = norm(ref)
    w = next((w for w in timing["words"] if w["start"] >= after - 0.05 and q in norm(w["text"])), None)
    return w["start"] if w else (None if strict else after)


if __name__ == "__main__":
    main()
