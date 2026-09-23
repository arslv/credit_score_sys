import subprocess
import sys

from tests.conftest import BACKEND_DIR


def _run_alembic(*args):
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        check=True,
    )


def test_migration_downgrades_and_upgrades_cleanly():
    _run_alembic("downgrade", "base")
    _run_alembic("upgrade", "head")
