"""보이스 디자인: 목소리를 문장으로 묘사해 Gemini 에 '나만의 목소리'를 만들어 저장한다.

만든 목소리는 voices.local.json (git 제외) 에 별칭으로 기록되고, 미리듣기는 voices/<별칭>.wav 로 저장된다.
이후 run.py / tts.py 의 --voice 에 별칭을 주면 그 목소리로 합성한다 (settings.local.json 의 "voice" 로 기본값 지정 가능).
- 지원 모델: gemini-3.8-flash-tts, gemini-3.8-flash-lite-tts (만든 모델에서만 쓴다)
- 프로젝트당 200개, 마지막 사용 후 1년 보관
- 나이·성별·음색·억양 같은 '바뀌지 않는 특징'만 1~2문장으로. 그때그때의 말투는 --style 로 준다.

사용법:
  python pipeline/voice_design.py 별칭 "묘사 1~2문장" [--gender female] [--lang ko-KR] [--model gemini-3.8-flash-tts]
  python pipeline/voice_design.py --list
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

from common import ROOT, get_api_key

REGISTRY = ROOT / "voices.local.json"
SAMPLES = ROOT / "voices"


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {}


def resolve_voice(name: str) -> tuple[str, str | None]:
    """별칭 → (보이스 ID, 만든 모델). 등록된 별칭이 아니면 (그대로, None) — 기본 목소리(Kore 등)."""
    v = load_registry().get(name)
    return (v["id"], v["model"]) if v else (name, None)


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="별칭 (영문 소문자-하이픈)")
    ap.add_argument("description", nargs="?", help="목소리 묘사 1~2문장")
    ap.add_argument("--gender", default="female", choices=["female", "male"])
    ap.add_argument("--lang", default="ko-KR")
    ap.add_argument("--model", default="gemini-3.8-flash-tts")
    ap.add_argument("--list", action="store_true", help="등록된 목소리 보기")
    a = ap.parse_args()

    reg = load_registry()
    if a.list or not a.name:
        for k, v in reg.items():
            print(f"{k}: {v['id']} ({v['model']}, {v['gender']}) — {v['description']}")
        return 0
    if not a.description:
        sys.exit("묘사 문장이 필요합니다.")
    if a.name in reg:
        sys.exit(f"'{a.name}' 은(는) 이미 있습니다: {reg[a.name]['id']}. 다른 별칭을 쓰세요.")

    client = genai.Client(api_key=get_api_key(),
                          http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=0)))
    try:
        v = client.voices.create(store=True, voice={
            "model": a.model,
            "type": "prompted",
            "display_name": a.name,
            "gender": a.gender,
            "language_code": a.lang,
            "prompted": {"input": a.description},
        })
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "429" in msg[:40] or "RESOURCE_EXHAUSTED" in msg:
            sys.exit(f"실패: 요청 한도 초과 ({a.model}). 하루 한도면 한국 시간 16시(태평양 자정) 이후 다시 실행하세요.")
        sys.exit(f"실패: 목소리 생성 — {msg[:300]}")

    vid = _get(v, "id")
    reg[a.name] = {"id": vid, "model": a.model, "gender": a.gender, "lang": a.lang,
                   "description": a.description, "created": time.strftime("%Y-%m-%d")}
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"목소리 생성: {a.name} = {vid}")

    sample = _get(v, "sample_audio")
    data = _get(sample, "data") if sample is not None else None
    if data:
        SAMPLES.mkdir(exist_ok=True)
        raw = base64.b64decode(data) if isinstance(data, str) else data
        out = SAMPLES / f"{a.name}.wav"
        out.write_bytes(raw)
        print(f"미리듣기: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
