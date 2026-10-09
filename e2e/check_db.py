import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "backend/dhurandhar_e2e_test.db"
con = sqlite3.connect(db)
tables = sorted(r[0] for r in con.execute("select name from sqlite_master where type='table'"))
print("TABLES:", tables)
con.close()
