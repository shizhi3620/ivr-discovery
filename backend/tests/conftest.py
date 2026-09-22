from __future__ import annotations

import os
import sys

import pytest

# Keep tests fast and deterministic even when a local .env adds call spacing.
os.environ["CALL_COOLDOWN_SECONDS"] = "0"

# Add backend dir to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Use a temporary DB for tests
import database as db


@pytest.fixture(autouse=True)
async def temp_db(tmp_path):
    """Use a fresh temporary database for each test."""
    db.DB_PATH = str(tmp_path / "test.db")
    await db.init_db()
    yield
    # DB file is cleaned up with tmp_path
