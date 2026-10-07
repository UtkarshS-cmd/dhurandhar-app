import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("TABLES:", [r[0] for r in cur.fetchall()])
cur.execute("SELECT version_num FROM alembic_version")
print("ALEMBIC_VERSION:", cur.fetchone())
cur.execute("SELECT COUNT(*) FROM reviews")
print("REVIEW_COUNT:", cur.fetchone()[0])
cur.execute("SELECT id, user_id, movie_id FROM reviews ORDER BY id LIMIT 8")
print("REVIEW_SAMPLE:", cur.fetchall())
cur.execute("SELECT COUNT(*) FROM review_likes")
print("LIKE_COUNT:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM movies")
print("MOVIE_COUNT:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM users")
print("USER_COUNT:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM bookings")
print("BOOKING_COUNT:", cur.fetchone()[0])
print("\nREVIEWS_TABLE_SQL:")
for row in cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reviews'"):
    print(row[0])
print("\nINDEXES/CONSTRAINTS:")
for row in cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('index','table','view') AND sql IS NOT NULL ORDER BY name"):
    print(row[0], '/', row[1], ':', (row[2] or '')[:160])
conn.close()


