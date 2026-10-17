import os
import sys

from auth import create_user

if __name__ == "__main__":
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        sys.exit("ADMIN_PASSWORD is not set. Add it to Backend/.env (see .env.example).")
    create_user(username, password, True)
    print("user created/updated")
