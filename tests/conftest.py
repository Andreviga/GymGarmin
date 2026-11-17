import pytest

from gymgarmin import db


@pytest.fixture(autouse=True)
def _fresh_postgres():
    """Com DATABASE_URL (Postgres), cada teste começa com o schema vazio."""
    url = db.database_url()
    if url:
        with db._pg_conn(url) as c:
            c.raw.cursor().execute("DROP SCHEMA public CASCADE")
            c.raw.cursor().execute("CREATE SCHEMA public")
        db._ready.clear()
    yield
