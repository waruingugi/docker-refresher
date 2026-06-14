import os, psycopg2

conn = psycopg2.connect(host=os.environ["DB_HOST"], dbname="appdb", user="postgres", password=os.environ["DB_PASS"])

cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS visits (id serial PRIMARY KEY, ts timestamptz DEFAULT now())")
conn.commit(); cur.close(); conn.close()
print("Migration complete")