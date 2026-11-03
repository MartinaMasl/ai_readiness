"""A stand-in for the Cloudflare D1 binding, on SQLite built from the
migration files (D1 is SQLite), so storage.py can be tested with its real
SQL: db.prepare(sql).bind(...).first() / .all() / .run()."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

MIGRATIONS = sorted((Path(__file__).parent.parent / "migrations").glob("*.sql"))


class FakeStatement:
    def __init__(self, connection, sql):
        self.connection, self.sql, self.args = connection, sql, ()

    def bind(self, *args):
        self.args = args
        return self

    async def first(self):
        return self.connection.execute(self.sql, self.args).fetchone()

    async def all(self):
        return SimpleNamespace(results=self.connection.execute(self.sql, self.args).fetchall())

    async def run(self):
        self.connection.execute(self.sql, self.args)


class FakeD1:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row  # rows readable by column name, like D1's
        for migration in MIGRATIONS:
            self.connection.executescript(migration.read_text())

    def prepare(self, sql):
        return FakeStatement(self.connection, sql)
