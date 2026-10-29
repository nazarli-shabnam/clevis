"""Every worker Postgres connection must bound its connect time."""

import event_consumer
import worker
from config import plain_db_url


def test_plain_db_url_strips_the_dialect_prefix_and_adds_a_connect_timeout():
    assert plain_db_url("postgresql+psycopg://u:p@db:5432/clevis") == "postgresql://u:p@db:5432/clevis?connect_timeout=5"


def test_plain_db_url_appends_to_an_existing_query_string():
    assert plain_db_url("postgresql+psycopg://u:p@db/clevis?sslmode=require") == (
        "postgresql://u:p@db/clevis?sslmode=require&connect_timeout=5"
    )


def test_the_job_poll_and_the_event_consumer_both_use_it():
    assert "connect_timeout=5" in worker._DB_URL
    assert "connect_timeout=5" in event_consumer._DB_URL
