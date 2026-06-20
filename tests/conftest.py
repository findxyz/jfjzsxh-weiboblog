"""测试夹具"""
import sqlite3
import pytest


@pytest.fixture
def mem_db():
    """内存 SQLite 连接，测试用"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def fixture_dir():
    """测试 fixture 目录路径"""
    import os
    return os.path.join(os.path.dirname(__file__), "fixtures")
