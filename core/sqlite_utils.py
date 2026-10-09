"""SQLite 查询结果的非空契约。"""

from sqlite3 import Row


def required_row(row: Row | None) -> Row:
    """读取聚合或刚写入的记录；违反必有一行的查询契约时明确报错。"""
    if row is None:
        raise RuntimeError("SQLite 查询未返回预期记录")
    return row
