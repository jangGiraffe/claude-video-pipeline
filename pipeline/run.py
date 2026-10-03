"""대본 → MP4 전체 파이프라인.

  python pipeline/run.py <작업이름> [--style "..."] [--voice Kore] [--from tts|transcribe|render] [--stills]

작업 폴더 work/<작업이름>/ 에 필요한 것:
  script.txt   대본 (필수)
  scenes.json  장면 기획서 (없으면 timing까지 만들고 멈춤 → Claude에게 기획 요청)

만들어지는 것:
  voice.wav, timing.json, subtitles.srt, output.mp4
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import setup_ffmpeg_path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
STEPS = ["tts", "transcribe", "render"]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("▶", " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, shell=(os.name == "nt" and cmd[0] == "npx"))
    if r.returncode != 0:
        sys.exit(f"실패: {cmd[0]} (exit {r.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--style", default="밝고 친근하지만 신뢰감 있는 테크 유튜버 내레이션. 적당히 빠른 템포.")
    ap.add_argument("--voice", default="Kore")
    ap.add_argument("--from", dest="start", choices=STEPS, default="tts", help="이 단계부터 다시 실행")
    ap.add_argument("--stills", action="store_true", help="MP4 대신 장면별 미리보기 PNG만 (work/<job>/stills/)")
    a = ap.parse_args()

    setup_ffmpeg_path()
    work = ROOT / "work" / a.job
    script = work / "script.txt"
    if not script.exists():
        sys.exit(f"{script} 이(가) 없습니다.")
    todo = STEPS[STEPS.index(a.start):]

    if "tts" in todo:
        run([PY, str(ROOT / "pipeline/tts.py"), str(script), str(work / "voice.wav"), "--voice", a.voice, "--style", a.style])
    if "transcribe" in todo:
        run([PY, str(ROOT / "pipeline/transcribe.py"), str(work / "voice.wav"), str(work), "--script", str(script), "--strict"])

    if not (work / "scenes.json").exists():
        print(f"\n타이밍 준비 완료. 이제 {work / 'scenes.json'} 장면 기획서가 필요합니다.")
        print(f"Claude에게: \"work/{a.job} 영상 장면 기획해줘\" 라고 요청한 뒤 --from render 로 다시 실행하세요.")
        return

    pub = ROOT / "video" / "public" / a.job
    pub.mkdir(parents=True, exist_ok=True)
    for f in ["voice.wav", "timing.json", "scenes.json"]:
        shutil.copy2(work / f, pub / f)
    props = f"--props={{\"job\":\"{a.job}\"}}"

    if a.stills:
        # 각 장면 중간 지점을 PNG로 찍어 렌더 전에 눈으로 확인
        timing = json.loads((work / "timing.json").read_text(encoding="utf-8"))
        scenes = json.loads((work / "scenes.json").read_text(encoding="utf-8"))["scenes"]
        starts = [0.0] + [resolve_sec(timing, s["at"]) for s in scenes[1:]]
        ends = starts[1:] + [timing["duration"]]
        out = work / "stills"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        for i, (s, e) in enumerate(zip(starts, ends)):
            frame = int((s + (e - s) * 0.7) * 30)
            run(["npx", "remotion", "still", "src/index.ts", "Explainer",
                 str(out / f"scene{i}_{scenes[i]['type']}.png"), f"--frame={frame}", props, "--log=error"], cwd=ROOT / "video")
        print(f"\n장면별 미리보기: {out}")
        return

    run(["npx", "remotion", "render", "src/index.ts", "Explainer", str(work / "output.mp4"), props, "--log=error"],
        cwd=ROOT / "video")
    print(f"\n완성: {work / 'output.mp4'}")


def resolve_sec(timing: dict, ref, after: float = 0.0) -> float:
    """video/src/timing.tsx 의 resolveSec 과 같은 규칙 (숫자=문장 시작, 문자열=어절 검색)."""
    if isinstance(ref, (int, float)):
        return timing["sentences"][int(ref)]["start"]
    norm = lambda s: re.sub(r"[\s\W_]+", "", s).lower()
    q = norm(ref)
    w = next((w for w in timing["words"] if w["start"] >= after - 0.05 and q in norm(w["text"])), None)
    return w["start"] if w else after


if __name__ == "__main__":
    main()
