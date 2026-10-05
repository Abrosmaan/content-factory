"""Аудио-операции через ffmpeg: склейка mp3-секций, двухпроходный
loudnorm до -16 LUFS, измерение фактической громкости.

ffmpeg обязан быть в PATH (есть в нашем Docker image).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

TARGET_LUFS = -16.0
TARGET_TRUE_PEAK = -1.5
TARGET_LRA = 11.0


def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg/ffprobe not found in PATH")


def concat(in_paths: list[Path], out_path: Path) -> None:
    """Склеить N mp3 в один без перекодирования (быстро) через concat demuxer."""
    _require_ffmpeg()
    if not in_paths:
        raise ValueError("concat: empty input list")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_path = Path(f.name)
        for p in in_paths:
            f.write(f"file '{p.resolve()}'\n")
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(out_path),
        ]
        subprocess.check_call(cmd)
    finally:
        list_path.unlink(missing_ok=True)


_LOUDNORM_JSON = re.compile(r"\{[^{}]*input_i[^{}]*\}", re.DOTALL)


def _parse_loudnorm_json(stderr: str) -> dict:
    """ffmpeg печатает JSON loudnorm в stderr — между прочих логов."""
    m = _LOUDNORM_JSON.search(stderr)
    if not m:
        raise RuntimeError("loudnorm JSON not found in ffmpeg output")
    return json.loads(m.group(0))


def duration_sec(path: Path) -> float:
    """Длительность mp3 в секундах через ffprobe."""
    _require_ffmpeg()
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


def measure_lufs(path: Path) -> float:
    """Вернуть интегральный LUFS (input_i из ffmpeg loudnorm)."""
    _require_ffmpeg()
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-af", "loudnorm=print_format=json",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = _parse_loudnorm_json(proc.stderr)
    return float(data["input_i"])


def normalize(in_path: Path, out_path: Path,
              target_lufs: float = TARGET_LUFS,
              target_tp: float = TARGET_TRUE_PEAK,
              target_lra: float = TARGET_LRA) -> dict:
    """Двухпроходный loudnorm. Возвращает измерения первого прохода."""
    _require_ffmpeg()
    # Pass 1: измерение
    cmd1 = [
        "ffmpeg", "-hide_banner", "-i", str(in_path),
        "-af",
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}:print_format=json",
        "-f", "null", "-",
    ]
    p1 = subprocess.run(cmd1, capture_output=True, text=True)
    meas = _parse_loudnorm_json(p1.stderr)
    # Pass 2: применение с измеренными параметрами
    flt = (
        f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={meas['input_i']}"
        f":measured_TP={meas['input_tp']}"
        f":measured_LRA={meas['input_lra']}"
        f":measured_thresh={meas['input_thresh']}"
        f":offset={meas['target_offset']}"
        f":linear=true:print_format=summary"
    )
    cmd2 = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(in_path),
        "-af", flt,
        "-ar", "44100", "-b:a", "128k",
        str(out_path),
    ]
    subprocess.check_call(cmd2)
    return meas


__all__ = [
    "TARGET_LUFS",
    "concat",
    "measure_lufs",
    "normalize",
]
