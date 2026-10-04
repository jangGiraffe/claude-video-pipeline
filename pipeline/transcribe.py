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

from common import load_pronounce, parse_script, setup_cuda_dlls, setup_ffmpeg_path, spoken_text

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
        # 대본 문장을 initial_prompt 로 주면 첫머리에서 그 문장을 되읊거나 'disadvant' 같은 말을 지어낸다
        # → 대본 속 영문 용어만 hotwords 로 준다 (철자 힌트 효과는 유지, 2026-10-04)
        hotwords=" ".join(dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z0-9]+", prompt)))[:300] if prompt else None,
        # 긴 음성(5분+)에서 앞 구간 결과를 다음 구간 프롬프트로 이어 쓰면 이미 지나간 문장을
        # 반복해 받아적는다 → 멀쩡한 TTS 를 '대본에 없는 말'로 오판 (2026-10-04)
        condition_on_previous_text=False,
    )
    words = [{"text": w.word.strip(), "start": w.start, "end": w.end}
             for s in segs for w in (s.words or []) if w.word.strip()]
    return words, info.duration


def run_weight(run: str) -> int:
    """대본에 없는 말 구간의 '의심 글자 수'. 영문은 빼고 센다:
    발음 사전으로 영어 용어를 한글로 읽혀도 Whisper 는 영문으로 받아적는 일이 많다 (에이유 업데이트 파일 → 'auupdatefile').
    TTS가 지어낸 말·반복은 한국어로 나온다."""
    return sum(1 for c in run if not ("a" <= c <= "z"))


def align(sentences: list[list[dict]], words: list[dict]):
    """대본 어절마다 Whisper 타이밍을 글자 정렬로 매핑.
    정렬은 '읽는 발음(spoken)' 글자로 하고, 결과 텍스트는 '화면 표기(display)'로 내보낸다."""
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
    tokens, s_chars, s_owner, spoken = [], [], [], []
    for si, sent in enumerate(sentences):
        for tok in sent:
            ti = len(tokens)
            tokens.append({"text": tok["display"], "sentence": si})
            spoken.append(tok["spoken"])
            for c in norm(tok["spoken"]):
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
    # 중간에 대본에 없는 말이 연속으로 나온 가장 긴 구간 (조각 합성 시 어느 조각이든 지시문을 읽을 수 있다)
    longest, cur, cur_start, spans = "", "", 0, []
    for j in range(first_hit, last_hit + 2):
        if j <= last_hit and not w_hit[j]:
            cur_start = j if not cur else cur_start
            cur += W[j]
            continue
        if run_weight(cur) >= 6:  # 연속 6글자 이상 대본에 없는 말 → 문제 구간
            spans.append((w_times[cur_start][0], w_times[j - 1][1]))
        if run_weight(cur) > run_weight(longest):
            longest = cur
        cur = ""
    # 대본 첫 어절이 다르게 받아적힌 것(예: JPASS → '제이패스')은 지시문 낭독이 아니다
    # → 대본 앞쪽의 정렬 안 된 글자 수만큼은 봐준다
    s_lead = next((i for i, m in enumerate(mapped) if m), len(S))
    lead_extra = first_hit - s_lead
    if lead_extra >= 4:
        spans.insert(0, (0.0, w_times[first_hit][0]))
    extra = {
        "lead": W[:first_hit] if lead_extra >= 4 else "",  # 대본 시작 전 말 → TTS가 지시문을 읽었을 가능성
        "tail": W[last_hit + 1:],                 # 대본 끝 이후 말 → 대개 Whisper 환각
        "inner_ratio": sum(1 for j in range(first_hit, last_hit + 1) if not w_hit[j]) / max(1, len(W)),
        "inner_run": longest,
        "bad_spans": spans,   # 문제 구간(초) → run.py 가 해당 TTS 조각만 다시 합성
    }

    # 대본 글자 → Whisper 글자 인덱스 (불일치 리포트용)
    s2w: list[int | None] = [None] * len(S)
    for a, b, size in SequenceMatcher(None, S, W, autojunk=False).get_matching_blocks():
        for k in range(size):
            s2w[a + k] = b + k

    # 어절 타이밍: 매핑된 글자들의 min start / max end
    mismatches = []
    for ti, tok in enumerate(tokens):
        idx = [i for i, o in enumerate(s_owner) if o == ti]
        ts = [mapped[i] for i in idx if mapped[i]]
        tok["start"] = min(t[0] for t in ts) if ts else None
        tok["end"] = max(t[1] for t in ts) if ts else None
        if idx and len(ts) / len(idx) < 0.7:
            # 앞뒤로 정렬된 Whisper 글자 사이 = 실제로 들린 말
            lo = next((s2w[i] for i in range(idx[0] - 1, -1, -1) if s2w[i] is not None), -1)
            hi = next((s2w[i] for i in range(idx[-1] + 1, len(S)) if s2w[i] is not None), len(W))
            mismatches.append((tok["text"], spoken[ti], W[lo + 1:hi]))
    extra["mismatches"] = mismatches

    # 매핑 실패한 어절은 앞뒤 어절 사이로 보간
    for ti, tok in enumerate(tokens):
        if tok["start"] is None:
            prev_end = next((tokens[j]["end"] for j in range(ti - 1, -1, -1) if tokens[j]["end"] is not None), 0.0)
            next_start = next((tokens[j]["start"] for j in range(ti + 1, len(tokens)) if tokens[j]["start"] is not None), prev_end)
            tok["start"], tok["end"] = prev_end, max(prev_end, next_start)

    sents = []
    for si in range(len(sentences)):
        ts = [t for t in tokens if t["sentence"] == si]
        sents.append({"text": " ".join(t["text"] for t in ts), "start": ts[0]["start"], "end": ts[-1]["end"]})
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

    if script:
        pairs = load_pronounce(Path(a.script))
        sentences = parse_script(script, pairs)
        words, duration = whisper_words(Path(a.audio), a.model, script)  # 표기 그대로(영문 용어 hotwords 용)
        sents, tokens, matched, extra = align(sentences, words)
        print(f"대본 정렬 일치율: {matched:.1%} / 음성 중 대본에 없는 말(중간): {extra['inner_ratio']:.1%}")
        if extra["tail"]:
            print(f"참고: 대본 끝 이후 인식된 말 '{extra['tail']}' (보통 Whisper 환각, 무시됨)")
        if extra["mismatches"]:
            print("발음 점검 (대본과 다르게 들린 어절 — 오독이면 pronounce.txt 나 {표기|발음} 으로 교정):")
            for disp, spk, heard in extra["mismatches"]:
                print(f"  {disp}" + (f" (읽기: {spk})" if spk != disp else "") + f" → 들린 말: '{heard}'")
        problems = []
        if run_weight(extra["inner_run"]) >= 6:
            problems.append(f"중간에 대본에 없는 말이 연속으로 있음: '{extra['inner_run']}' (지시문 낭독/환각 의심)")
        # 영문은 빼고 센다: 첫머리 무음에서 Whisper 가 'disadvant' 같은 영어를 지어내는 일이 있고,
        # TTS 가 한국어 말투 지시를 읽어 버린 경우는 한국어로 나온다 (2026-10-04)
        if run_weight(extra["lead"]) >= 4:
            problems.append(f"대본 시작 전에 다른 말이 있음: '{extra['lead']}' (TTS가 지시문을 읽었을 수 있음)")
        if matched < 0.9:
            problems.append(f"대본 일치율 낮음 {matched:.1%} (TTS가 문장을 빠뜨렸을 수 있음)")
        if extra["inner_ratio"] > 0.1:
            problems.append(f"음성에 대본에 없는 말이 {extra['inner_ratio']:.1%}")
        for p in problems:
            print("경고:", p)
        bad = out / "bad_spans.json"
        bad.unlink(missing_ok=True)
        if problems:
            bad.write_text(json.dumps({"spans": extra["bad_spans"], "whole": matched < 0.9}), encoding="utf-8")
        if problems and a.strict:
            raise SystemExit("검증 실패 (--strict)")
    else:
        words, duration = whisper_words(Path(a.audio), a.model, None)
        tokens = [{**w, "sentence": 0} for w in words]
        sents = [{"text": " ".join(w["text"] for w in words), "start": words[0]["start"], "end": words[-1]["end"]}] if words else []

    (out / "timing.json").write_text(json.dumps(
        {"duration": duration, "sentences": sents, "words": tokens}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "subtitles.srt").write_text(to_srt(sents), encoding="utf-8")
    print(f"saved {out / 'timing.json'}, {out / 'subtitles.srt'} ({len(sents)} sentences, {len(tokens)} words, {duration:.1f}s)")


if __name__ == "__main__":
    main()
