"""
Session path resolution helpers.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional


def _wsl_path_from_windows(raw: str) -> Optional[str]:
    """Convert C:\\Users\\... style path to /mnt/c/Users/... when possible."""
    if not raw or len(raw) < 3:
        return None
    if raw[1:3] != ":\\":
        return None
    drive = raw[0].lower()
    remainder = raw[3:].replace("\\", "/")
    return f"/mnt/{drive}/{remainder}"


def _windows_path_from_wsl_mount(raw: str) -> Optional[str]:
    """Convert /mnt/c/Users/... style path to C:\\Users\\... when possible."""
    if not raw or not raw.startswith("/mnt/") or len(raw) < 7:
        return None
    drive = raw[5:6]
    if not drive.isalpha() or raw[6:7] != "/":
        return None
    remainder = raw[7:].replace("/", "\\")
    return f"{drive.upper()}:\\{remainder}"


def _wsl_unc_from_linux(raw: str) -> Iterable[str]:
    """Convert /home/... style WSL paths to \\\\wsl.localhost\\<distro>\\... candidates."""
    if not raw or not raw.startswith("/"):
        return []
    distros: list[str] = []
    preferred = os.environ.get("OPENCLAW_WSL_DISTRO")
    if preferred:
        distros.append(preferred)
    for name in ("Ubuntu-24.04", "Ubuntu"):
        if name not in distros:
            distros.append(name)

    suffix = raw.lstrip("/").replace("/", "\\")
    return [f"\\\\wsl.localhost\\{distro}\\{suffix}" for distro in distros]


def _candidate_variants(raw: Optional[str]) -> list[str]:
    """Expand a raw candidate into runtime-specific variants."""
    if not raw:
        return []

    variants = [raw]
    for converted in (
        _wsl_path_from_windows(raw),
        _windows_path_from_wsl_mount(raw),
    ):
        if converted:
            variants.append(converted)
    variants.extend(_wsl_unc_from_linux(raw))
    return variants


def _openclaw_candidates() -> Iterable[str]:
    """Generate likely OpenClaw sessions directories for local/dev environments."""
    home = Path.home()
    yield str(home / ".openclaw" / "agents" / "main" / "sessions")
    yield "/root/.openclaw/agents/main/sessions"

    # Common Windows locations (native path and WSL mount path)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        yield str(Path(userprofile) / ".openclaw" / "agents" / "main" / "sessions")
        wsl = _wsl_path_from_windows(userprofile)
        if wsl:
            yield f"{wsl}/.openclaw/agents/main/sessions"

    # If this project lives in ~/.openclaw/projects/<repo>, infer sibling agents path.
    cwd = Path.cwd().resolve()
    parts = list(cwd.parts)
    for i in range(len(parts) - 1):
        if parts[i] == ".openclaw" and parts[i + 1] == "projects":
            base = Path(*parts[: i + 1])
            yield str(base / "agents" / "main" / "sessions")
            break

    # Common hard-coded machine path used in this repo docs.
    yield "/home/agents/openclaw-local/core/agents/main/sessions"


def resolve_sessions_dir(configured: Optional[str]) -> str:
    """
    Resolve the most likely existing sessions directory.

    Order:
      1. Configured path (config.yaml)
      2. SESSIONS_DIR env var
      3. WSL-converted forms of 1/2 when they look like Windows paths
      4. Known fallback locations
    """
    candidates: list[str] = []

    env_path = os.environ.get("SESSIONS_DIR")
    for raw in (env_path, configured):
        candidates.extend(_candidate_variants(raw))

    for raw in _openclaw_candidates():
        candidates.extend(_candidate_variants(raw))

    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        norm = os.path.normpath(candidate)
        if norm in seen:
            continue
        seen.add(norm)
        try:
            if Path(norm).exists():
                return norm
        except (PermissionError, OSError):
            continue

    # Fall back to configured value for transparency if nothing exists.
    return env_path or configured or "/root/.openclaw/agents/main/sessions"
