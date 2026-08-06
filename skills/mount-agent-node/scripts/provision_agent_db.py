#!/usr/bin/env python3
"""Provision a dedicated database + user for a new agent-node.

Usage: provision_agent_db.py <postgres|mysql|mariadb> <admin-url>
Prints DB_NAME / DB_USER / DB_PASSWORD on success.

Only needs python3 (any OS): the required driver (psycopg / PyMySQL) is
installed into a reusable venv under the system temp dir on first run.
"""

import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlsplit, unquote

VENV = os.path.join(tempfile.gettempdir(), "mount-agent-node-provision-venv")
VENV_PY = os.path.join(VENV, "Scripts" if os.name == "nt" else "bin",
                       "python.exe" if os.name == "nt" else "python3")
DRIVER_PKG = {"postgres": "psycopg[binary]", "mysql": "PyMySQL", "mariadb": "PyMySQL"}


def ensure_driver(driver: str):
    mod = "psycopg" if driver == "postgres" else "pymysql"
    try:
        return __import__(mod)
    except ImportError:
        pass
    if os.path.abspath(sys.prefix) == os.path.abspath(VENV):
        # already inside our venv, just missing this driver
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", DRIVER_PKG[driver]], check=True)
        return __import__(mod)
    if not os.path.exists(VENV_PY):
        subprocess.run([sys.executable, "-m", "venv", VENV], check=True)
    subprocess.run([VENV_PY, "-m", "pip", "install", "-q", DRIVER_PKG[driver]], check=True)
    # re-run ourselves inside the venv (subprocess, not execv: portable to Windows)
    result = subprocess.run([VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])
    sys.exit(result.returncode)


def provision_postgres(psycopg, admin_url, db, user, password):
    # CREATE DATABASE refuses to run inside a transaction -> autocommit
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db}"')
    # role + grants connect to the NEW database so the schema grant lands there
    u = urlsplit(admin_url)
    new_db_url = u._replace(path="/" + db).geturl()
    with psycopg.connect(new_db_url, autocommit=True) as conn:
        conn.execute(f'CREATE ROLE "{user}" WITH LOGIN PASSWORD \'{password}\'')
        conn.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{db}" TO "{user}"')
        conn.execute(f'GRANT ALL PRIVILEGES ON SCHEMA public TO "{user}"')


def provision_mysql(pymysql, admin_url, db, user, password):
    u = urlsplit(admin_url)
    conn = pymysql.connect(
        host=u.hostname,
        port=u.port or 3306,
        user=unquote(u.username or "root"),
        password=unquote(u.password or ""),
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{db}`")  # db name is a generated uuid, not user input
        cur.execute("CREATE USER %s@'%%' IDENTIFIED BY %s", (user, password))
        cur.execute(f"GRANT ALL PRIVILEGES ON `{db}`.* TO %s@'%%'", (user,))
        cur.execute("FLUSH PRIVILEGES")
    conn.close()


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in DRIVER_PKG:
        sys.exit("usage: provision_agent_db.py <postgres|mysql|mariadb> <admin-url>")
    driver, admin_url = sys.argv[1], sys.argv[2]

    db = str(uuid.uuid4()).lower()
    user = db.replace("-", "")
    password = secrets.token_hex(16)

    mod = ensure_driver(driver)
    if driver == "postgres":
        provision_postgres(mod, admin_url, db, user, password)
    else:
        provision_mysql(mod, admin_url, db, user, password)

    print(f"DB_NAME={db}")
    print(f"DB_USER={user}")
    print(f"DB_PASSWORD={password}")


if __name__ == "__main__":
    main()
