from __future__ import annotations

import ctypes
import importlib.metadata as metadata
import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request

_DLL_DIR_HANDLES = []


def get_pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not-installed"


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return proc.returncode, proc.stdout.strip()
    except FileNotFoundError:
        return 127, "command not found"


def fetch_status() -> str:
    url = "http://127.0.0.1:5000/api/status"
    try:
        with urllib.request.urlopen(url, timeout=2.5) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        provider = payload.get("data", {}).get("ai_provider")
        state = payload.get("data", {}).get("state")
        return f"api/status => ai_provider={provider}, state={state}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return "api/status => app not running or no response"


def enable_windows_nvidia_dll_search() -> list[str]:
    if os.name != "nt":
        return []

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    venv_site = os.path.join(project_root, ".venv", "Lib", "site-packages")
    nvidia_root = os.path.join(venv_site, "nvidia")
    if not os.path.isdir(nvidia_root):
        return []

    path_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    seen = {os.path.normcase(os.path.normpath(p)) for p in path_parts}
    added_dirs: list[str] = []

    try:
        pkg_names = sorted(os.listdir(nvidia_root))
    except Exception:
        pkg_names = []

    for pkg_name in pkg_names:
        bin_dir = os.path.join(nvidia_root, pkg_name, "bin")
        if not os.path.isdir(bin_dir):
            continue
        normalized = os.path.normcase(os.path.normpath(bin_dir))
        if normalized not in seen:
            path_parts.insert(0, bin_dir)
            seen.add(normalized)
            added_dirs.append(bin_dir)
        if hasattr(os, "add_dll_directory"):
            try:
                _DLL_DIR_HANDLES.append(os.add_dll_directory(bin_dir))
            except Exception:
                pass

    if added_dirs:
        os.environ["PATH"] = os.pathsep.join(path_parts)
    return added_dirs


def probe_ort_cuda_dll(ort_module) -> tuple[bool, str]:
    if os.name != "nt":
        return True, "skip (non-windows)"

    ort_file = getattr(ort_module, "__file__", "")
    if not ort_file:
        return False, "onnxruntime module path not found"

    cuda_dll = os.path.join(os.path.dirname(ort_file), "capi", "onnxruntime_providers_cuda.dll")
    if not os.path.isfile(cuda_dll):
        return False, f"missing file: {cuda_dll}"

    try:
        ctypes.WinDLL(cuda_dll)
        return True, f"loaded: {cuda_dll}"
    except OSError as exc:
        return False, str(exc)


def main() -> int:
    print("=== Age Kiosk GPU Diagnose ===")
    print(f"python: {sys.version.split()[0]}")
    print(f"platform: {platform.platform()}")
    print(f"cwd: {os.getcwd()}")
    print()

    dll_dirs = enable_windows_nvidia_dll_search()
    if dll_dirs:
        print("nvidia dll search dirs:")
        for d in dll_dirs:
            print(f"- {d}")
        print()

    print("packages:")
    for pkg in ("onnxruntime", "onnxruntime-gpu", "onnxruntime-directml", "insightface"):
        print(f"- {pkg}: {get_pkg_version(pkg)}")
    print()

    code, out = run_cmd(["nvidia-smi"])
    print("nvidia-smi:")
    if code == 0:
        lines = out.splitlines()
        head = "\n".join(lines[:20])
        print(head)
    else:
        print(f"failed ({code}): {out}")
    print()

    try:
        import onnxruntime as ort  # type: ignore
        try:
            if hasattr(ort, "preload_dlls"):
                ort.preload_dlls()
        except Exception as exc:
            print(f"onnxruntime preload_dlls failed: {exc}")
    except Exception as exc:  # pragma: no cover
        print(f"onnxruntime import failed: {exc}")
        print(fetch_status())
        return 2

    providers = ort.get_available_providers()
    cuda_dll_ok, cuda_dll_msg = probe_ort_cuda_dll(ort)
    print(f"onnxruntime providers: {providers}")
    print(f"cuda_dll_probe: {'ok' if cuda_dll_ok else 'failed'} ({cuda_dll_msg})")
    print(fetch_status())

    if "CUDAExecutionProvider" in providers and cuda_dll_ok:
        print("result: CUDA provider is available")
        return 0

    print("result: CUDA provider NOT available or not usable")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
