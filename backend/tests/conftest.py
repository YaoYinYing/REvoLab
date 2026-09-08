from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from revolab.db import Base, get_session
from revolab.main import app


def _enable_fk(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", _enable_fk)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as value:
        yield value


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with Session(engine) as value:
            yield value

    app.dependency_overrides[get_session] = override
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()
