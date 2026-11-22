"""Deixa a biblioteca CUDA visível para o faster-whisper."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from discord_stt.paths import ROOT


def cublas_directories() -> list[Path]:
    directories: list[Path] = []
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        directories.append(Path(conda_prefix) / "lib")
    home = Path.home()
    for base in (
        home / "anaconda3" / "envs",
        home / "miniconda3" / "envs",
        home / "mambaforge" / "envs",
        home / "miniforge3" / "envs",
    ):
        if base.is_dir():
            directories.extend(sorted(path for path in base.glob("*/lib") if path.is_dir()))
    directories.extend(
        [
            Path("/usr/local/cuda/lib64"),
            Path("/usr/local/lib/ollama/cuda_v12"),
        ]
    )
    return directories


def windows_cuda_directories() -> list[Path]:
    directories = [
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path(sys.prefix) / "Library" / "bin",
    ]
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        directories.append(Path(conda_prefix) / "Library" / "bin")
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        directories.append(Path(cuda_path) / "bin")
    home = Path.home()
    for base in (home / "anaconda3" / "envs", home / "miniconda3" / "envs"):
        if base.is_dir():
            directories.extend(path / "Library" / "bin" for path in base.iterdir() if path.is_dir())
    return directories


def ensure_windows_cuda_dlls() -> None:
    found = [path for path in windows_cuda_directories() if path.is_dir() and any(path.glob("cublas*.dll"))]
    if not found:
        return
    for path in found:
        try:
            os.add_dll_directory(str(path))
        except (AttributeError, OSError):
            pass
    os.environ["PATH"] = os.pathsep.join([str(path) for path in found] + [os.environ.get("PATH", "")])


def ensure_cuda_libraries() -> None:
    """Reexecuta o processo no Linux quando encontra o libcublas fora do caminho padrão."""
    if sys.platform == "win32":
        ensure_windows_cuda_dlls()
        return
    chosen = next((path for path in cublas_directories() if (path / "libcublas.so.12").exists()), None)
    if chosen is None:
        return
    chosen_str = str(chosen)
    parts = [part for part in os.environ.get("LD_LIBRARY_PATH", "").split(":") if part]
    if chosen_str in parts:
        return
    os.environ["LD_LIBRARY_PATH"] = ":".join([chosen_str, *parts])
    os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.execv(sys.executable, [sys.executable, "-m", "discord_stt", *sys.argv[1:]])
