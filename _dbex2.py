import sqlite3
import sys

path = sys.argv[1]
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("SELECT id, user_id, rating, title, body, spoiler, created_at FROM reviews")
print("REVIEWS:", cur.fetchall())
cur.execute("SELECT id, user_id, status FROM bookings")
print("BOOKINGS:", cur.fetchall())
cur.execute("SELECT id, user_id, movie_id, title FROM movies")
print("MOVIES:", cur.fetchall())
cur.execute("SELECT id, review_id, user_id FROM review_likes")
print("LIKES:", cur.fetchall())
cur.execute("SELECT id, name, email FROM users")
print("USERS:", cur.fetchall())
conn.close()
