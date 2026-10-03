"""파이프라인 공통 유틸: API 키 읽기, CUDA DLL 경로, ffmpeg 경로, 대본 파싱(표기/발음 분리)."""
import glob
import os
import re
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ───────── 대본: 화면 표기 vs 읽는 발음 ─────────
# 인라인:  {M/M으로|맨먼스로}  → 자막엔 "M/M으로", TTS는 "맨먼스로"
# 사전:    "표기 = 발음" 줄. 뒤에 오는 파일이 앞을 덮어쓴다.
#          pronounce.txt        저장소 공통 (공개 · 일반 용어만)
#          pronounce.local.txt  로컬 전용 (git 제외 · 회사/대외비 용어)
#          work/<job>/pronounce.txt  작업별
MARK = re.compile(r"\{([^{}|]*)\|([^{}]*)\}")
TOKEN = re.compile(r"(?:\{[^{}]*\}|\S)+")          # 공백 기준 어절, {…} 묶음은 쪼개지 않음
SENT_END = re.compile(r"(?<=[.!?。…])\s+(?![^{]*\})")  # {…} 안의 마침표에서는 문장을 자르지 않음
JOSA = {"은": "는", "이": "가", "을": "를", "과": "와", "으로": "로"}  # 받침 있음 → 없음


def load_pronounce(script_path: Path) -> list[tuple[str, str]]:
    pairs: dict[str, str] = {}
    for f in [ROOT / "pronounce.txt", ROOT / "pronounce.local.txt", Path(script_path).parent / "pronounce.txt"]:
        if f.exists():
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = (x.strip() for x in line.split("=", 1))
                    if k and v:
                        pairs[k] = v
    return sorted(pairs.items(), key=lambda kv: -len(kv[0]))  # 긴 표기부터


def _has_batchim(ch: str) -> bool:
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def _apply_dict(tok: str, pairs: list[tuple[str, str]]) -> str:
    """사전 표기를 어절 안에서 {표기|발음} 으로 감싼다. 바로 뒤 조사가 발음 받침과 안 맞으면 고친다."""
    if "{" in tok or not pairs:
        return tok  # 이미 인라인 지정
    table = dict(pairs)
    # 영문·숫자에 붙은 경우는 제외 (AU 가 AUTO 안에서 바뀌지 않게). 한 어절에 여러 개면 모두 (D-6~D-0)
    pat = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k, _ in pairs) + r")(?![A-Za-z0-9])")
    matches = list(pat.finditer(tok))
    if not matches:
        return tok
    out, pos = [], 0
    for n, mt in enumerate(matches):
        disp, spoken = mt.group(1), table[mt.group(1)]
        out.append(tok[pos:mt.start()])
        pos = mt.end()
        rest = tok[pos:]
        j = re.match(r"^(으로|은|는|이|가|을|를|과|와|로)(?=[\W_]*$)", rest) if n == len(matches) - 1 else None
        if j and "가" <= spoken[-1] <= "힣":
            j = j.group(1)
            fixed = j
            if not _has_batchim(spoken[-1]) and j in JOSA:
                fixed = JOSA[j]
            elif _has_batchim(spoken[-1]) and j in JOSA.values():
                fixed = {v: k for k, v in JOSA.items()}[j]
            out.append(f"{{{disp}{j}|{spoken}{fixed}}}")
            pos += len(j)
        else:
            out.append(f"{{{disp}|{spoken}}}")
    out.append(tok[pos:])
    return "".join(out)


def parse_script(raw: str, pairs: list[tuple[str, str]] | None = None) -> list[list[dict]]:
    """대본 → 문장 목록, 문장 = [{display, spoken}] 어절 목록."""
    pairs = pairs or []
    out = []
    for line in raw.strip().splitlines():
        for sent in SENT_END.split(line.strip()):
            toks = []
            for t in TOKEN.findall(sent):
                t = _apply_dict(t, pairs)
                toks.append({"display": MARK.sub(r"\1", t), "spoken": MARK.sub(r"\2", t)})
            if toks:
                out.append(toks)
    return out


def spoken_text(raw: str, pairs: list[tuple[str, str]] | None = None) -> str:
    """TTS에 넣을 텍스트 (줄 구조 유지, 빈 줄 = 단락 구분 유지)."""
    lines = []
    for line in raw.strip().splitlines():
        sents = parse_script(line, pairs) if line.strip() else []
        lines.append(" ".join(" ".join(t["spoken"] for t in s) for s in sents))
    return "\n".join(lines)


def load_settings() -> dict:
    """환경별 설정. 저장소 루트의 settings.local.json (git 제외) 을 읽는다. 없으면 빈 설정.
    예시는 settings.example.json. 환경변수 VIDEO_OUTPUT_DIR 이 있으면 output_dir 보다 우선한다."""
    import json
    f = ROOT / "settings.local.json"
    s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if os.environ.get("VIDEO_OUTPUT_DIR"):
        s["output_dir"] = os.environ["VIDEO_OUTPUT_DIR"]
    return s


def get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key and sys.platform == "win32":
        # 앱 재시작 전이라 프로세스에 반영 안 됐으면 사용자 환경변수(레지스트리)에서 직접 읽는다
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            key, _ = winreg.QueryValueEx(k, "GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 환경변수가 없습니다.")
    return key.strip()


def setup_cuda_dlls() -> None:
    """pip로 설치한 nvidia-cublas/cudnn DLL을 Windows에서 찾을 수 있게 등록."""
    for sp in site.getsitepackages():
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def setup_ffmpeg_path() -> None:
    """winget 설치 직후라 PATH에 ffmpeg이 없으면 사용자/시스템 PATH를 다시 읽어 붙인다."""
    if sys.platform != "win32":
        return
    import winreg
    paths = []
    for root, sub in [(winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]:
        try:
            with winreg.OpenKey(root, sub) as k:
                paths.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    os.environ["PATH"] = os.environ["PATH"] + os.pathsep + os.pathsep.join(paths)
