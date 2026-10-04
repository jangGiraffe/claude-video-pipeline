"""대본 텍스트 → Gemini TTS → WAV.

긴 대본을 한 번에 합성하면 중간부터 목소리 톤이 바뀌는(드리프트) 문제가 있어
단락(빈 줄) 또는 몇 문장 단위로 나눠 합성하고, 앞뒤 무음을 다듬어 일정한 간격으로 이어 붙인다.
발음 사전(pronounce*.txt)과 인라인 {표기|발음} 을 적용한 '읽는 텍스트'를 합성한다.

같은 voice 를 지정해도 호출마다 목소리 높낮이가 크게 달라질 때가 있다(특히 --style 을 줄 때,
여성 Kore 가 남성 음역까지 내려가기도 함). 그래서 조각마다 기본 주파수(F0)를 재서
전체 중앙값에서 PITCH_TOL 이상 벗어난 조각은 다시 합성한다.

요청 수 절약 (2026-10-04, 모델별 하루 한도 RPD·분당 한도 RPM 초과를 겪고 정리):
- 단락을 하나씩 따로 합성하지 않고, 이어지는 단락을 --chunk 줄 수까지 묶어 한 번에 합성한다.
- SDK 의 숨은 자동 재시도(429 를 받으면 조용히 다시 요청)를 끈다. 재시도는 아래 루프에서만, 보이게 한다.
- 호출 간격을 --rpm 이하로 맞춘다 (병렬 3개가 몰려 분당 한도를 넘지 않게).
- 하루 한도 초과(429 ...PerDay)는 재시도해도 소용없으므로 바로 exit 3 (run.py 가 안내).

사용법:
  python pipeline/tts.py 대본.txt 출력.wav [--voice Kore] [--style "차분하고 친근하게"] [--chunk 7]
"""
import argparse
import base64
import hashlib
import io
import json
import queue
import re
import statistics
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from common import gemini_client, load_pronounce, spoken_text
from voice_design import resolve_voice

# 무료 3.8 Flash 를 먼저 쓰고, 실패하면 run.py 가 유료 2.5 Pro 로 전체를 다시 합성한다 (비용 절약).
# - 3.8 Flash(무료): --style(speech_metadata) 지원. 목소리가 흔들리거나 혼잡 시 응답이 없을 때가 있다.
# - 2.5 Pro(유료): 조각 간 목소리가 가장 일정 (2026-10-03 측정: 8조각 편차 ±9%, 46초). --style 미지원.
FREE_MODEL = "gemini-3.8-flash-tts"
PAID_MODEL = "gemini-2.5-pro-preview-tts"
MODEL = FREE_MODEL
ATTEMPTS = 5           # 호출 하나당 최대 시도 횟수 (무료 단계는 run.py 가 줄여서 빨리 포기)
STYLE_MODELS = {"gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"}  # speech_metadata.style 지원 모델
RATE, WIDTH, CH = 24000, 2, 1
GAP_SEC = 0.5          # 조각 사이 무음
SILENCE_AMP = 300      # 이보다 작은 진폭은 무음으로 보고 앞뒤를 자른다
KEEP_SEC = 0.08        # 자를 때 남겨 둘 여유
CALL_TIMEOUT = 90      # API 한 번 호출 제한 시간(초). 응답 없이 멈추는 경우가 있다 (Pro 는 보통 10초 안팎)
PITCH_TOL = 0.12       # 조각 F0 가 중앙값에서 이 비율 넘게 벗어나면 다른 목소리로 보고 재합성
PITCH_ROUNDS = 4       # 목소리 맞추기 최대 반복
RPM = 8                # 분당 최대 호출 수 (AI Studio 기준 TTS 모델 한도 10 보다 여유 있게)
EXIT_DAILY_QUOTA = 3   # 하루 한도 초과 시 종료 코드 (run.py 와 같게 유지)


class DailyQuotaError(RuntimeError):
    """모델별 하루 요청 한도(RPD) 초과. 초기화까지 남은 시간은 서버 응답의 'retry in …' 로 안내된다."""


_rate_lock = threading.Lock()
_next_call = 0.0


def _throttle() -> None:
    """모든 스레드를 합쳐 호출 간격을 60/RPM 초 이상으로 맞춘다."""
    global _next_call
    with _rate_lock:
        now = time.monotonic()
        wait = _next_call - now
        _next_call = max(now, _next_call) + 60 / RPM
    if wait > 0:
        time.sleep(wait)


def _is_429(e: Exception) -> bool:
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    return code == 429 or "429" in str(e)[:40] or "RESOURCE_EXHAUSTED" in str(e)


def _is_daily_quota(e: Exception) -> bool:
    msg = str(e)
    return _is_429(e) and ("PerDay" in msg or "per day" in msg.lower())


def _call_with_timeout(fn, timeout: float):
    """데몬 스레드로 호출 → 제한 시간이 지나면 버리고 TimeoutError (멈춘 스레드가 종료를 막지 않게)."""
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: q.put(_safe(fn)), daemon=True).start()
    try:
        ok, val = q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"TTS 응답 없음 ({timeout}s)")
    if not ok:
        raise val
    return val


def _safe(fn):
    try:
        return True, fn()
    except Exception as e:  # noqa: BLE001
        return False, e


def synthesize_pcm(client, text: str, voice: str, style: str | None) -> bytes:
    content = {"type": "text", "text": text}
    if style and MODEL in STYLE_MODELS:
        # 말투 지시는 본문에 섞으면 소리 내어 읽어버린다 → speech_metadata.style로 분리
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    for attempt in range(ATTEMPTS):
        _throttle()
        try:
            interaction = _call_with_timeout(lambda: client.interactions.create(
                model=MODEL,
                input=[{"type": "user_input", "content": [content]}],
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": voice}]},
            ), CALL_TIMEOUT)
            break
        except Exception as e:  # 레이트 리밋·일시 오류·무응답 → 백오프 후 재시도
            if _is_daily_quota(e):
                m = re.search(r"retry in ([0-9hms.]+)", str(e))
                raise DailyQuotaError(f"{MODEL} 하루 요청 한도 초과"
                                      + (f" (서버 안내: {m.group(1)} 뒤 재시도)" if m else "")) from e
            code = getattr(e, "status_code", None) or getattr(e, "code", None)
            if attempt == ATTEMPTS - 1 or (isinstance(code, int) and 400 <= code < 500 and code != 429):
                raise  # 요청 자체가 잘못된 오류(400 등)는 재시도해도 같다
            if _is_429(e):
                print(f"  재시도 {attempt + 1}: 분당 한도 초과 → 60초 대기")
                time.sleep(60)
            else:
                print(f"  재시도 {attempt + 1}: {str(e)[:120]}")
                time.sleep(10 * 2 ** attempt)
    data = base64.b64decode(interaction.output_audio.data)
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as w:
            fmt = (w.getframerate(), w.getsampwidth(), w.getnchannels())
            if fmt != (RATE, WIDTH, CH):
                raise RuntimeError(f"예상과 다른 오디오 형식 {fmt}")
            return w.readframes(w.getnframes())
    return data  # 헤더 없는 PCM(24kHz, mono, 16bit)


def trim(pcm: bytes) -> bytes:
    s = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
    loud = np.flatnonzero(np.abs(s.astype(np.int32)) > SILENCE_AMP)
    if not len(loud):
        return pcm
    keep = int(KEEP_SEC * RATE)
    return s[max(0, loud[0] - keep): min(len(s), loud[-1] + keep)].tobytes()


def pitch(pcm: bytes) -> float:
    """유성음 구간의 기본 주파수(Hz) 중앙값. 자기상관 방식."""
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
    n, lo, hi = 1024, RATE // 400, RATE // 70
    f0s = []
    for i in range(0, len(x) - n, n // 2):
        fr = x[i:i + n]
        if np.abs(fr).mean() < 800:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[n - 1:]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            f0s.append(RATE / lag)
    return float(np.median(f0s)) if f0s else 0.0


def chunk_lines(text: str, size: int) -> list[str]:
    """단락(빈 줄) 경계에서만 자르되, 이어지는 단락을 size 줄까지 한 조각으로 묶는다.
    한 단락이 size 줄보다 길면 그 단락만 size 줄씩 자른다. 조각 안의 단락 사이는 빈 줄로 남겨 쉼을 둔다."""
    paras = []
    for para in text.split("\n\n"):
        lines = [l.strip() for l in para.splitlines() if l.strip()]
        paras += [lines[i:i + size] for i in range(0, len(lines), size)]
    chunks, cur, n = [], [], 0
    for p in paras:
        if cur and n + len(p) > size:
            chunks.append("\n\n".join("\n".join(x) for x in cur))
            cur, n = [], 0
        cur.append(p)
        n += len(p)
    if cur:
        chunks.append("\n\n".join("\n".join(x) for x in cur))
    return chunks


def main() -> int:
    global MODEL, ATTEMPTS, RPM
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="대본 txt 파일")
    ap.add_argument("out", help="출력 wav 경로")
    ap.add_argument("--voice", default="Kore", help="기본 목소리 이름(Kore 등) 또는 voice_design.py 로 만든 별칭")
    ap.add_argument("--style", default=None, help="말투 지시 (예: '차분하고 친근한 유튜버 톤')")
    ap.add_argument("--chunk", type=int, default=12, help="한 번에 합성할 최대 줄 수 (이어지는 단락을 이만큼까지 묶음)")
    ap.add_argument("--rpm", type=int, default=RPM, help="분당 최대 호출 수")
    ap.add_argument("--model", default=MODEL, help=f"TTS 모델 (기본 {MODEL}, 유료 {PAID_MODEL})")
    ap.add_argument("--attempts", type=int, default=ATTEMPTS, help="호출 하나당 최대 시도 횟수")
    ap.add_argument("--strict", action="store_true", help="목소리 높낮이가 끝까지 안 맞으면 실패(exit 2)")
    a = ap.parse_args()
    MODEL, ATTEMPTS, RPM = a.model, a.attempts, a.rpm
    if a.style and MODEL not in STYLE_MODELS:
        print(f"참고: {MODEL} 은 --style 을 지원하지 않아 무시합니다 (지원: {', '.join(sorted(STYLE_MODELS))})")
        a.style = None

    script = Path(a.script)
    text = spoken_text(script.read_text(encoding="utf-8-sig"), load_pronounce(script))
    chunks = chunk_lines(text, a.chunk)
    voice_id, voice_model = resolve_voice(a.voice)  # 별칭이면 디자인한 보이스 ID 로
    if voice_model and voice_model != MODEL:
        print(f"실패: 목소리 '{a.voice}' 는 {voice_model} 로 만든 것이라 {MODEL} 에서 쓸 수 없습니다.")
        return 1
    print(f"TTS {len(chunks)}조각 합성 (model={MODEL}, voice={a.voice})")

    # 조각별 캐시: 검증에서 문제가 난 조각만 지우고 다시 돌리면 나머지는 재사용된다
    out = Path(a.out)
    cache = out.parent / "tts_cache"
    cache.mkdir(parents=True, exist_ok=True)
    # SDK 기본값은 429·5xx 를 조용히 재시도해 요청 수가 몇 배로 늘고 '응답 없음'처럼 보인다 → 끈다
    client = gemini_client()

    def key(c: str) -> str:
        return hashlib.sha1(f"{MODEL}|{a.voice}|{a.style}|{c}".encode()).hexdigest()[:16] + ".pcm"

    def get(c: str) -> bytes:
        f = cache / key(c)
        if f.exists():
            return f.read_bytes()
        pcm = trim(synthesize_pcm(client, c, voice_id, a.style))
        f.write_bytes(pcm)
        return pcm

    # 목소리 맞추기: 중앙값에서 벗어난 조각은 캐시를 지우고 다시 합성
    pitch_ok = False
    for rnd in range(PITCH_ROUNDS + 1):
        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                pcms = list(ex.map(get, chunks))
        except DailyQuotaError as e:
            print(f"실패: {e}. 다른 TTS 모델은 한도가 따로라 --model 로 바꿔 진행할 수 있음 (AI Studio '비율 제한' 페이지 참고)")
            return EXIT_DAILY_QUOTA
        except Exception as e:  # noqa: BLE001 — 무응답·오류: run.py 가 다음 모델로 넘어간다
            print(f"실패: TTS 호출 ({MODEL}) — {str(e)[:150]}")
            return 1
        f0 = [pitch(p) for p in pcms]
        ref = statistics.median(v for v in f0 if v) if any(f0) else 0
        bad = [i for i, v in enumerate(f0) if ref and abs(v - ref) / ref > PITCH_TOL]
        print(f"  목소리 높낮이(Hz): " + " ".join(f"{v:.0f}" + ("*" if i in bad else "") for i, v in enumerate(f0))
              + f"  (기준 {ref:.0f})")
        if not bad:
            pitch_ok = True
            break
        if rnd == PITCH_ROUNDS:
            print(f"경고: {len(bad)}개 조각의 목소리가 끝까지 다름 (* 표시). --style 을 빼거나 바꿔 보세요.")
            break
        for i in bad:
            (cache / key(chunks[i])).unlink(missing_ok=True)
        print(f"  목소리가 다른 조각 {len(bad)}개 재합성 ({rnd + 1}/{PITCH_ROUNDS})")

    # 조각별 시간 구간 기록 (run.py 가 문제 구간 → 조각 매핑에 사용)
    meta, t = [], 0.0
    for c, p, v in zip(chunks, pcms, f0):
        d = len(p) / WIDTH / RATE
        meta.append({"text": c, "start": round(t, 3), "end": round(t + d, 3), "f0": round(v, 1), "cache": key(c)})
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
    return 2 if a.strict and not pitch_ok else 0


if __name__ == "__main__":
    sys.exit(main())
