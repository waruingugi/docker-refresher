from flask import Flask
import os, socket, psycopg2, redis

app = Flask(__name__)

DB_HOST = os.environ.get("DB_HOST", "pg")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")

@app.route("/")
def home():
    # Postgres: durable visit log
    conn = psycopg2.connect(host=DB_HOST, dbname="appdb", user="postgres", password="secret")

    cur = conn.cursor()
    cur.execute("INSERT INTO visits DEFAULT VALUES")
    cur.execute("SELECT count(*) FROM visits")
    total = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()

    # Redis: fast volatile counter
    hits = redis.Redis(host=REDIS_HOST, port=6379).incr("page_hits")

    return f"host={socket.gethostname()} db_visits={total} redis_hits={hits}\n"
