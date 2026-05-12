#!/usr/bin/env python3
"""Validate every action manifest in this pack against ActionManifest.

Glob: ``actions/*/manifest.yml`` — matches the runtime loader in
``backend/app/actions/packs.py`` (``pack.actions_dir.glob("*/manifest.yml")``).

Also parses ``pack.yml`` as YAML if present (no Pydantic model exists
upstream for it; the runtime treats it as a loose dict).

Usage (local):
    # From the root of a labdog-playbooks checkout:
    python scripts/validate_manifests.py

    # With ActionManifest sourced from a labdog checkout:
    LABDOG_SRC=/path/to/labdog python scripts/validate_manifests.py

    # With an explicit pack root:
    python scripts/validate_manifests.py --pack-root /path/to/pack

Exit codes:
    0  — all manifests valid
    1  — one or more manifests failed validation
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_action_manifest_class():
    """Import ActionManifest from a LABDOG_SRC tree, or fail loudly.

    The class lives at ``app.actions.manifest.ActionManifest`` in the
    labdog repo. We prepend ``<LABDOG_SRC>/backend`` to ``sys.path`` and
    import directly — only ``pydantic`` is needed (no DB/Celery deps).
    """
    labdog_src = os.environ.get("LABDOG_SRC", "").strip()
    if not labdog_src:
        print(
            "error: LABDOG_SRC is not set. Point it at a labdog checkout "
            "(the root, not the backend/ subdir).",
            file=sys.stderr,
        )
        sys.exit(1)

    backend_path = Path(labdog_src) / "backend"
    if not backend_path.is_dir():
        print(
            f"error: LABDOG_SRC={labdog_src!r} but {backend_path} does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.path.insert(0, str(backend_path))

    try:
        from app.actions.manifest import ActionManifest  # type: ignore[import]

        return ActionManifest
    except ModuleNotFoundError as exc:
        print(
            f"error: cannot import ActionManifest: {exc}. "
            "Check the LABDOG_SRC path or pip install pydantic.",
            file=sys.stderr,
        )
        sys.exit(1)


def validate_pack_yml(pack_root: Path) -> bool:
    """Parse pack.yml (if present) as YAML. Returns True if ok or absent."""
    import yaml

    pack_yml = pack_root / "pack.yml"
    if not pack_yml.exists():
        return True
    try:
        data = yaml.safe_load(pack_yml.read_text())
        if not isinstance(data, dict):
            print(f"FAIL  pack.yml — must be a YAML mapping", file=sys.stderr)
            return False
        print(f"  ok  pack.yml")
        return True
    except yaml.YAMLError as exc:
        print(f"FAIL  pack.yml — YAML parse error: {exc}", file=sys.stderr)
        return False


def validate_manifests(pack_root: Path, ActionManifest) -> tuple[int, int]:  # noqa: N803
    """Validate every ``actions/*/manifest.yml`` under *pack_root*.

    Returns (ok_count, fail_count).
    """
    import yaml
    from pydantic import ValidationError

    actions_dir = pack_root / "actions"
    if not actions_dir.is_dir():
        print(f"warning: no actions/ directory found at {pack_root}.", file=sys.stderr)
        return 0, 0

    manifest_paths = sorted(actions_dir.glob("*/manifest.yml"))
    if not manifest_paths:
        print(
            f"warning: no manifests matched actions/*/manifest.yml under {pack_root}.",
            file=sys.stderr,
        )
        return 0, 0

    ok = 0
    fail = 0
    for path in manifest_paths:
        rel = path.relative_to(pack_root)
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            print(f"FAIL  {rel} — YAML parse error: {exc}", file=sys.stderr)
            fail += 1
            continue

        try:
            ActionManifest.model_validate(raw)
            print(f"  ok  {rel}")
            ok += 1
        except ValidationError as exc:
            print(f"FAIL  {rel}", file=sys.stderr)
            for err in exc.errors():
                loc = ".".join(str(s) for s in err["loc"]) if err["loc"] else "(root)"
                print(f"        {loc}: {err['msg']}", file=sys.stderr)
            fail += 1

    return ok, fail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pack-root",
        type=Path,
        default=Path("."),
        help="Root of the labdog-playbooks checkout (default: current directory).",
    )
    args = parser.parse_args()

    pack_root = args.pack_root.resolve()
    print(f"Validating manifests under: {pack_root}")

    ActionManifest = _load_action_manifest_class()  # noqa: N806

    pack_ok = validate_pack_yml(pack_root)
    ok, fail = validate_manifests(pack_root, ActionManifest)

    suffix = " (pack.yml error)" if not pack_ok else ""
    print(f"\nResult: {ok} passed, {fail} failed{suffix}")

    if fail > 0 or not pack_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
