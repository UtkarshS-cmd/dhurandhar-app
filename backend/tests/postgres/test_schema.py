"""PostgreSQL schema tests — inspect the live catalog, not the ORM models."""
from sqlalchemy import text


EXPECTED_TABLES = {
    "users",
    "movies",
    "cities",
    "theaters",
    "screens",
    "seats",
    "shows",
    "show_seats",
    "bookings",
    "booking_seats",
    "payment_attempts",
    "payment_webhook_events",
    "reviews",
    "review_likes",
    "newsletter_subscribers",
    "contact_messages",
    "admin_audit_logs",
}


def test_expected_tables_exist(pg_session):
    tables = {
        row[0]
        for row in pg_session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).all()
    }
    assert EXPECTED_TABLES <= tables


def test_unique_constraints_exist(pg_session):
    rows = pg_session.execute(
        text(
            "SELECT tc.table_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema "
            "WHERE tc.constraint_type = 'UNIQUE' AND tc.table_schema = 'public'"
        )
    ).all()
    by_table: dict[str, set[str]] = {}
    for table, column in rows:
        by_table.setdefault(table, set()).add(column)
    assert "email" in by_table.get("users", set())
    assert "title" in by_table.get("movies", set())
    assert {"screen_id", "row_label", "seat_number"} <= by_table.get("seats", set())
    assert {"screen_id", "show_date", "show_time"} <= by_table.get("shows", set())
    assert {"show_id", "seat_id"} <= by_table.get("show_seats", set())
    assert {"booking_id", "seat_id"} <= by_table.get("booking_seats", set())
    assert {"provider", "event_id"} <= by_table.get("payment_webhook_events", set())
    assert {"user_id", "movie_id"} <= by_table.get("reviews", set())
    assert {"review_id", "user_id"} <= by_table.get("review_likes", set())
