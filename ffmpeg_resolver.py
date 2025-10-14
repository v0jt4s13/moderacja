import os
import shutil
from typing import Optional

_FFMPEG_EXE: Optional[str] = None
_FFPROBE_EXE: Optional[str] = None


def _is_file(path: Optional[str]) -> bool:
    return bool(path) and os.path.isfile(path)  # type: ignore[arg-type]


def _candidate_paths(binary_name: str) -> list[str]:
    paths: list[str] = []
    # Environment overrides
    env_map = {
        "ffmpeg": [os.getenv("FFMPEG_EXE"), os.getenv("FFMPEG")],
        "ffprobe": [os.getenv("FFPROBE_EXE"), os.getenv("FFPROBE")],
    }
    for p in env_map.get(binary_name, []):
        if p:
            paths.append(p)

    # Same dir as counterpart (e.g., if FFMPEG_EXE is set)
    if binary_name == "ffprobe":
        ffmpeg_env = os.getenv("FFMPEG_EXE") or os.getenv("FFMPEG")
        if ffmpeg_env:
            d = os.path.dirname(ffmpeg_env)
            probe_guess = os.path.join(d, "ffprobe.exe" if os.name == "nt" else "ffprobe")
            paths.append(probe_guess)
    elif binary_name == "ffmpeg":
        ffprobe_env = os.getenv("FFPROBE_EXE") or os.getenv("FFPROBE")
        if ffprobe_env:
            d = os.path.dirname(ffprobe_env)
            ffm_guess = os.path.join(d, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            paths.append(ffm_guess)

    # Common install locations
    if os.name == "nt":
        common_dirs = [
            r"C:\\ffmpeg\\bin",
            r"C:\\Program Files\\ffmpeg\\bin",
            r"C:\\Program Files (x86)\\ffmpeg\\bin",
            r"C:\\ProgramData\\chocolatey\\bin",
        ]
        exe = f"{binary_name}.exe"
        for d in common_dirs:
            paths.append(os.path.join(d, exe))
    else:
        for d in ["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/opt/local/bin"]:
            paths.append(os.path.join(d, binary_name))

    return paths


def _resolve(binary_name: str) -> str:
    # 1) shutil.which on PATH
    p = shutil.which(binary_name)
    if p:
        return p
    # 2) environment and common locations
    for cand in _candidate_paths(binary_name):
        if _is_file(cand):
            return cand
    # 3) fallback to bare name (may fail at runtime, but keeps behavior)
    return binary_name


def get_ffmpeg_exe() -> str:
    global _FFMPEG_EXE
    if not _FFMPEG_EXE:
        _FFMPEG_EXE = _resolve("ffmpeg")
    return _FFMPEG_EXE


def get_ffprobe_exe() -> str:
    global _FFPROBE_EXE
    if not _FFPROBE_EXE:
        _FFPROBE_EXE = _resolve("ffprobe")
    return _FFPROBE_EXE

