import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

url = os.environ["DATABASE_URL"]

with psycopg.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("select now();")
        print("Connected. Server time:", cur.fetchone()[0])
