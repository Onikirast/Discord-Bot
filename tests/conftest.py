"""
conftest.py

Shared pytest fixtures. pytest automatically discovers this file --
any fixture defined here is available to every test file in this
directory without needing to import it explicitly.
"""

import pytest_asyncio

import database


@pytest_asyncio.fixture
async def temp_db(tmp_path, monkeypatch):
    """Point database.py at a fresh, temporary SQLite file for the
    duration of one test, instead of the real bot.db.

    tmp_path is a built-in pytest fixture: a unique temporary directory
    pytest creates and cleans up automatically, one per test. monkeypatch
    is also built-in -- it temporarily overrides an attribute (here,
    database.DB_PATH) and automatically restores the original value
    after the test finishes, even if the test fails.
    """
    db_file = tmp_path / "test_bot.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_file))
    await database.init_db()
    yield database
