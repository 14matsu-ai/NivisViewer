from __future__ import annotations

import json

from app.app_paths import resolve_app_paths
from app.frozen_smoke import run_frozen_smoke


def test_smoke_writes_machine_readable_result(qapp, tmp_path):
    profile = tmp_path / "smoke profile"
    paths = resolve_app_paths(
        profile_override=str(profile),
        source_root=tmp_path,
    )
    result_path = tmp_path / "result.json"

    success = run_frozen_smoke(qapp, paths, result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert success
    assert result["success"] is True
    assert all(result["checks"].values())
    assert result["errors"] == []
