from __future__ import annotations

import pytest

from feature_flags.config import Settings


@pytest.mark.parametrize(
    "given",
    [
        "postgres://flags:secret@db.render.internal:5432/flags",
        "postgresql://flags:secret@db.render.internal:5432/flags",
    ],
)
def test_managed_postgres_urls_are_rewritten_to_psycopg3(given: str) -> None:
    settings = Settings(_env_file=None, database_url=given)
    assert (
        settings.database_url == "postgresql+psycopg://flags:secret@db.render.internal:5432/flags"
    )


@pytest.mark.parametrize(
    "given",
    ["sqlite://", "sqlite:///./flags.db", "postgresql+psycopg://flags:flags@db:5432/flags"],
)
def test_explicit_driver_urls_are_left_alone(given: str) -> None:
    assert Settings(_env_file=None, database_url=given).database_url == given
