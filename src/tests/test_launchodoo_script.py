import os
import subprocess
from pathlib import Path


def test_launch_wrapper_handles_an_unset_source_clone_archive(tmp_path: Path):
    fake_godoo = tmp_path / "godoo"
    fake_godoo.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_godoo.chmod(0o755)

    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.pop("SOURCE_CLONE_ARCHIVE", None)
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"

    result = subprocess.run(
        [repository_root / "scripts" / "launchodoo.sh", "--launch-only"],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
