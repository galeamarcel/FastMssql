from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".env.sql-auth.local"


def password(label: str) -> str:
    return f"{label}_{secrets.token_urlsafe(24)}!Aa1"


def render() -> str:
    values = {
        "FASTMSSQL_SQL_AUTH_HOST": "127.0.0.1",
        "FASTMSSQL_SQL_AUTH_PORT": "14334",
        "FASTMSSQL_SQL_AUTH_DATABASE": "fastmssql_validation",
        "FASTMSSQL_SQL_AUTH_UPSTREAM_DATABASE": (
            "fastmssql_upstream_regression"
        ),
        "FASTMSSQL_SQL_AUTH_SA_USER": "sa",
        "FASTMSSQL_SQL_AUTH_SA_PASSWORD": password("Sa"),
        "FASTMSSQL_SQL_AUTH_OWNER_USER": "fastmssql_owner",
        "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD": password("Owner"),
        "FASTMSSQL_SQL_AUTH_READONLY_USER": "fastmssql_readonly",
        "FASTMSSQL_SQL_AUTH_READONLY_PASSWORD": password("Readonly"),
        "FASTMSSQL_SQL_AUTH_DENIED_USER": "fastmssql_denied",
        "FASTMSSQL_SQL_AUTH_DENIED_PASSWORD": password("Denied"),
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if OUTPUT.exists() and not args.force:
        print(f"kept existing {OUTPUT}")
        return 0
    OUTPUT.write_text(render(), encoding="utf-8")
    os.chmod(OUTPUT, 0o600)
    print(f"created {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
