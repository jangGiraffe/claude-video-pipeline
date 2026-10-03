"""대본 텍스트 → Gemini TTS → WAV.

긴 대본을 한 번에 합성하면 중간부터 목소리 톤이 바뀌는(드리프트) 문제가 있어
단락(빈 줄) 또는 몇 문장 단위로 나눠 합성하고, 앞뒤 무음을 다듬어 일정한 간격으로 이어 붙인다.
발음 사전(pronounce.txt)과 인라인 {표기|발음} 을 적용한 '읽는 텍스트'를 합성한다.

사용법:
  python pipeline/tts.py 대본.txt 출력.wav [--voice Kore] [--style "차분하고 친근하게"] [--chunk 7]
"""
import argparse
import array
import base64
import hashlib
import io
import json
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from google import genai

from common import get_api_key, load_pronounce, spoken_text

MODEL = "gemini-3.8-flash-tts"
RATE, WIDTH, CH = 24000, 2, 1
GAP_SEC = 0.5          # 조각 사이 무음
SILENCE_AMP = 300      # 이보다 작은 진폭은 무음으로 보고 앞뒤를 자른다
KEEP_SEC = 0.08        # 자를 때 남겨 둘 여유


def synthesize_pcm(client, text: str, voice: str, style: str | None) -> bytes:
    content = {"type": "text", "text": text}
    if style:
        # 말투 지시는 본문에 섞으면 소리 내어 읽어버린다 → speech_metadata.style로 분리
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    for attempt in range(4):
        try:
            interaction = client.interactions.create(
                model=MODEL,
                input=[{"type": "user_input", "content": [content]}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
            )
            break
        except Exception as e:  # 레이트 리밋·일시 오류 → 백오프 후 재시도
            if attempt == 3:
                raise
            print(f"  재시도 {attempt + 1}: {e}")
            time.sleep(5 * 2 ** attempt)
    data = base64.b64decode(interaction.output_audio.data)
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as w:
            fmt = (w.getframerate(), w.getsampwidth(), w.getnchannels())
            if fmt != (RATE, WIDTH, CH):
                raise RuntimeError(f"예상과 다른 오디오 형식 {fmt}")
            return w.readframes(w.getnframes())
    return data  # 헤더 없는 PCM(24kHz, mono, 16bit)


def trim(pcm: bytes) -> bytes:
    s = array.array("h", pcm[: len(pcm) // 2 * 2])
    loud = [i for i, v in enumerate(s) if abs(v) > SILENCE_AMP]
    if not loud:
        return pcm
    keep = int(KEEP_SEC * RATE)
    a, b = max(0, loud[0] - keep), min(len(s), loud[-1] + keep)
    return s[a:b].tobytes()


def chunk_lines(text: str, size: int) -> list[str]:
    """빈 줄로 나눈 단락을 우선 존중하고, 단락이 길면 size 줄씩 자른다."""
    chunks = []
    for para in text.split("\n\n"):
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        for i in range(0, len(lines), size):
            chunks.append("\n".join(lines[i:i + size]))
    return [c for c in chunks if c]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="대본 txt 파일")
    ap.add_argument("out", help="출력 wav 경로")
    ap.add_argument("--voice", default="Kore")
    ap.add_argument("--style", default=None, help="말투 지시 (예: '차분하고 친근한 유튜버 톤')")
    ap.add_argument("--chunk", type=int, default=7, help="한 번에 합성할 최대 줄 수")
    a = ap.parse_args()

    script = Path(a.script)
    text = spoken_text(script.read_text(encoding="utf-8-sig"), load_pronounce(script))
    chunks = chunk_lines(text, a.chunk)
    print(f"TTS {len(chunks)}조각 합성 (voice={a.voice})")

    # 조각별 캐시: 검증에서 문제가 난 조각만 지우고 다시 돌리면 나머지는 재사용된다
    out = Path(a.out)
    cache = out.parent / "tts_cache"
    cache.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=get_api_key())

    def key(c: str) -> str:
        return hashlib.sha1(f"{MODEL}|{a.voice}|{a.style}|{c}".encode()).hexdigest()[:16] + ".pcm"

    def get(c: str) -> bytes:
        f = cache / key(c)
        if f.exists():
            return f.read_bytes()
        pcm = trim(synthesize_pcm(client, c, a.voice, a.style))
        f.write_bytes(pcm)
        return pcm

    with ThreadPoolExecutor(max_workers=3) as ex:
        pcms = list(ex.map(get, chunks))

    # 조각별 시간 구간 기록 (run.py 가 문제 구간 → 조각 매핑에 사용)
    meta, t = [], 0.0
    for c, p in zip(chunks, pcms):
        d = len(p) / WIDTH / RATE
        meta.append({"text": c, "start": round(t, 3), "end": round(t + d, 3),
                     "cache": key(c)})
        t += d + GAP_SEC
    (out.parent / "tts_chunks.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    gap = b"\x00" * int(GAP_SEC * RATE) * WIDTH
    with wave.open(str(out), "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(WIDTH)
        w.setframerate(RATE)
        w.writeframes(gap.join(pcms))
    total = sum(len(p) for p in pcms) / WIDTH / RATE + GAP_SEC * (len(pcms) - 1)
    print(f"saved {out} ({total:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
