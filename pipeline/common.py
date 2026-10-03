"""파이프라인 공통 유틸: API 키 읽기, CUDA DLL 경로, ffmpeg 경로."""
import glob
import os
import site
import sys


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
