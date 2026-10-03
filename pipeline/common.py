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
# 사전:    pronounce.txt (저장소 루트 + work/<job>/) 의 "표기 = 발음" 줄
MARK = re.compile(r"\{([^{}|]*)\|([^{}]*)\}")
TOKEN = re.compile(r"(?:\{[^{}]*\}|\S)+")          # 공백 기준 어절, {…} 묶음은 쪼개지 않음
SENT_END = re.compile(r"(?<=[.!?。…])\s+(?![^{]*\})")  # {…} 안의 마침표에서는 문장을 자르지 않음
JOSA = {"은": "는", "이": "가", "을": "를", "과": "와", "으로": "로"}  # 받침 있음 → 없음


def load_pronounce(script_path: Path) -> list[tuple[str, str]]:
    pairs: dict[str, str] = {}
    for f in [ROOT / "pronounce.txt", Path(script_path).parent / "pronounce.txt"]:
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
    if "{" in tok:
        return tok  # 이미 인라인 지정
    for disp, spoken in pairs:
        i = tok.find(disp)
        if i < 0:
            continue
        rest = tok[i + len(disp):]
        m = re.match(r"^(으로|은|는|이|가|을|를|과|와|로)(?=[\W_]*$)", rest)
        if m and spoken and "가" <= spoken[-1] <= "힣":
            j = m.group(1)
            batchim = _has_batchim(spoken[-1])
            fixed = j
            if not batchim and j in JOSA:
                fixed = JOSA[j]
            elif batchim and j in JOSA.values():
                fixed = {v: k for k, v in JOSA.items()}[j]
            return f"{tok[:i]}{{{disp}{j}|{spoken}{fixed}}}{rest[len(j):]}"
        return f"{tok[:i]}{{{disp}|{spoken}}}{rest}"
    return tok


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
