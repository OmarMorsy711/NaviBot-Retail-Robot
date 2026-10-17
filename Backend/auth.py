import os
import psycopg
from passlib.context import CryptContext
from dotenv import load_dotenv
load_dotenv()


DATABASE_URL = os.environ["DATABASE_URL"]
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_conn() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)

def create_user(username: str, password_plain: str, is_active: bool = True) -> None:
    password_hash = pwd_context.hash(password_plain)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into users (username, password_hash, is_active)
                values (%s, %s, %s)
                on conflict (username) do update
                set password_hash = excluded.password_hash,
                    is_active = excluded.is_active
                """,
                (username, password_hash, is_active),
            )

def verify_login(username: str, password_plain: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select password_hash, is_active from users where username = %s",
                (username,),
            )
            row = cur.fetchone()

            if not row:
                return False

            password_hash, is_active = row[0], row[1]
            if not is_active:
                return False

            if not pwd_context.verify(password_plain, password_hash):
                return False

            cur.execute(
                "update users set last_login = now() where username = %s",
                (username,),
            )
            return True
