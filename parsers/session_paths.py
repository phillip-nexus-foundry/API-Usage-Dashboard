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

    if configured:
        candidates.append(configured)

    env_path = os.environ.get("SESSIONS_DIR")
    if env_path:
        candidates.append(env_path)

    for raw in (configured, env_path):
        wsl = _wsl_path_from_windows(raw or "")
        if wsl:
            candidates.append(wsl)

    candidates.extend(_openclaw_candidates())

    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        norm = str(Path(candidate))
        if norm in seen:
            continue
        seen.add(norm)
        try:
            if Path(norm).exists():
                return norm
        except (PermissionError, OSError):
            continue

    # Fall back to configured value for transparency if nothing exists.
    return configured or env_path or "/root/.openclaw/agents/main/sessions"
