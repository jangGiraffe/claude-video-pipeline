"""음성 → Whisper(GPU) 타이밍 → 대본 원문으로 교정한 자막.

Whisper가 받아쓴 글자는 틀릴 수 있으므로 '언제 말했는지'만 쓰고,
글자는 대본 원문을 글자 단위로 정렬(difflib)해 타이밍을 옮겨 붙인다.

사용법:
  python pipeline/transcribe.py 음성.wav 출력폴더 [--script 대본.txt] [--model large-v3]

출력:
  timing.json  { duration, sentences:[{text,start,end}], words:[{text,start,end,sentence}] }
  subtitles.srt
"""
import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from common import setup_cuda_dlls, setup_ffmpeg_path

setup_cuda_dlls()
setup_ffmpeg_path()
from faster_whisper import WhisperModel  # noqa: E402

NON_CHAR = re.compile(r"[\s\W_]+", re.UNICODE)


def norm(s: str) -> str:
    return NON_CHAR.sub("", s).lower()


def whisper_words(audio: Path, model_name: str, prompt: str | None):
    model = WhisperModel(model_name, device="cuda", compute_type="float16")
    segs, info = model.transcribe(
        str(audio), language="ko", word_timestamps=True, vad_filter=True,
        initial_prompt=prompt[:400] if prompt else None,
    )
    words = [{"text": w.word.strip(), "start": w.start, "end": w.end}
             for s in segs for w in (s.words or []) if w.word.strip()]
    return words, info.duration


def split_sentences(script: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。…])\s+|\n+", script.strip())
    return [p.strip() for p in parts if p.strip()]


def align(script: str, words: list[dict]):
    """대본 어절마다 Whisper 타이밍을 글자 정렬로 매핑."""
    # Whisper 쪽: 정규화 글자열 + 글자별 (start,end)
    w_chars, w_times = [], []
    for w in words:
        n = norm(w["text"])
        if not n:
            continue
        step = (w["end"] - w["start"]) / len(n)
        for i, c in enumerate(n):
            w_chars.append(c)
            w_times.append((w["start"] + step * i, w["start"] + step * (i + 1)))
    W = "".join(w_chars)

    # 대본 쪽: 문장 → 어절 → 정규화 글자 (어절 인덱스 기록)
    sentences = split_sentences(script)
    tokens, s_chars, s_owner = [], [], []
    for si, sent in enumerate(sentences):
        for tok in sent.split():
            ti = len(tokens)
            tokens.append({"text": tok, "sentence": si})
            for c in norm(tok):
                s_chars.append(c)
                s_owner.append(ti)
    S = "".join(s_chars)

    # 대본 글자 i → Whisper 글자 j
    mapped: list[tuple[float, float] | None] = [None] * len(S)
    w_hit = [False] * len(W)
    for a, b, size in SequenceMatcher(None, S, W, autojunk=False).get_matching_blocks():
        for k in range(size):
            mapped[a + k] = w_times[b + k]
            w_hit[b + k] = True

    # 역방향 검사: 음성에 대본에 없는 말이 섞였나 (TTS가 지시문을 읽음 / Whisper 환각)
    first_hit = next((j for j, h in enumerate(w_hit) if h), len(W))
    last_hit = max((j for j, h in enumerate(w_hit) if h), default=-1)
    extra = {
        "lead": W[:first_hit],                    # 대본 시작 전 말 → TTS가 지시문을 읽었을 가능성
        "tail": W[last_hit + 1:],                 # 대본 끝 이후 말 → 대개 Whisper 환각
        "inner_ratio": sum(1 for j in range(first_hit, last_hit + 1) if not w_hit[j]) / max(1, len(W)),
    }

    # 어절 타이밍: 매핑된 글자들의 min start / max end
    for ti, tok in enumerate(tokens):
        ts = [mapped[i] for i, o in enumerate(s_owner) if o == ti and mapped[i]]
        tok["start"] = min(t[0] for t in ts) if ts else None
        tok["end"] = max(t[1] for t in ts) if ts else None

    # 매핑 실패한 어절은 앞뒤 어절 사이로 보간
    for ti, tok in enumerate(tokens):
        if tok["start"] is None:
            prev_end = next((tokens[j]["end"] for j in range(ti - 1, -1, -1) if tokens[j]["end"] is not None), 0.0)
            next_start = next((tokens[j]["start"] for j in range(ti + 1, len(tokens)) if tokens[j]["start"] is not None), prev_end)
            tok["start"], tok["end"] = prev_end, max(prev_end, next_start)

    sents = []
    for si, text in enumerate(sentences):
        ts = [t for t in tokens if t["sentence"] == si]
        sents.append({"text": text, "start": ts[0]["start"], "end": ts[-1]["end"]})
    matched = sum(1 for m in mapped if m) / max(1, len(mapped))
    return sents, tokens, matched, extra


def to_srt(sents: list[dict]) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    return "\n".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n"
                     for i, s in enumerate(sents, 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("outdir")
    ap.add_argument("--script", help="대본 txt (있으면 자막 글자를 대본으로 교정)")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--strict", action="store_true", help="검증 경고가 있으면 실패 처리")
    a = ap.parse_args()

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    script = Path(a.script).read_text(encoding="utf-8-sig") if a.script else None

    words, duration = whisper_words(Path(a.audio), a.model, script)
    if script:
        sents, tokens, matched, extra = align(script, words)
        print(f"대본 정렬 일치율: {matched:.1%} / 음성 중 대본에 없는 말(중간): {extra['inner_ratio']:.1%}")
        if extra["tail"]:
            print(f"참고: 대본 끝 이후 인식된 말 '{extra['tail']}' (보통 Whisper 환각, 무시됨)")
        problems = []
        if len(extra["lead"]) >= 4:
            problems.append(f"대본 시작 전에 다른 말이 있음: '{extra['lead']}' (TTS가 지시문을 읽었을 수 있음)")
        if matched < 0.9:
            problems.append(f"대본 일치율 낮음 {matched:.1%} (TTS가 문장을 빠뜨렸을 수 있음)")
        if extra["inner_ratio"] > 0.1:
            problems.append(f"음성에 대본에 없는 말이 {extra['inner_ratio']:.1%}")
        for p in problems:
            print("경고:", p)
        if problems and a.strict:
            raise SystemExit("검증 실패 (--strict)")
    else:
        tokens = [{**w, "sentence": 0} for w in words]
        sents = [{"text": " ".join(w["text"] for w in words), "start": words[0]["start"], "end": words[-1]["end"]}] if words else []

    (out / "timing.json").write_text(json.dumps(
        {"duration": duration, "sentences": sents, "words": tokens}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "subtitles.srt").write_text(to_srt(sents), encoding="utf-8")
    print(f"saved {out / 'timing.json'}, {out / 'subtitles.srt'} ({len(sents)} sentences, {len(tokens)} words, {duration:.1f}s)")


if __name__ == "__main__":
    main()
