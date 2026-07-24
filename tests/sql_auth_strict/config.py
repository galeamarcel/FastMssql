from __future__ import annotations

from dataclasses import dataclass, field
import os


def _required(name: str, *, required: bool) -> str:
    value = os.getenv(name, "")
    if required and not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


@dataclass(frozen=True, repr=False)
class SqlAuthConfig:
    host: str
    port: int
    database: str
    upstream_database: str
    sa_user: str
    sa_password: str = field(repr=False)
    owner_user: str
    owner_password: str = field(repr=False)
    readonly_user: str
    readonly_password: str = field(repr=False)
    denied_user: str
    denied_password: str = field(repr=False)

    @classmethod
    def from_env(cls, *, require_all: bool = True) -> "SqlAuthConfig":
        return cls(
            host=os.getenv("FASTMSSQL_SQL_AUTH_HOST", "127.0.0.1"),
            port=int(os.getenv("FASTMSSQL_SQL_AUTH_PORT", "14334")),
            database=os.getenv(
                "FASTMSSQL_SQL_AUTH_DATABASE", "fastmssql_validation"
            ),
            upstream_database=os.getenv(
                "FASTMSSQL_SQL_AUTH_UPSTREAM_DATABASE",
                "fastmssql_upstream_regression",
            ),
            sa_user=os.getenv("FASTMSSQL_SQL_AUTH_SA_USER", "sa"),
            sa_password=_required(
                "FASTMSSQL_SQL_AUTH_SA_PASSWORD", required=require_all
            ),
            owner_user=os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_USER", "fastmssql_owner"
            ),
            owner_password=_required(
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", required=require_all
            ),
            readonly_user=os.getenv(
                "FASTMSSQL_SQL_AUTH_READONLY_USER", "fastmssql_readonly"
            ),
            readonly_password=_required(
                "FASTMSSQL_SQL_AUTH_READONLY_PASSWORD", required=require_all
            ),
            denied_user=os.getenv(
                "FASTMSSQL_SQL_AUTH_DENIED_USER", "fastmssql_denied"
            ),
            denied_password=_required(
                "FASTMSSQL_SQL_AUTH_DENIED_PASSWORD", required=require_all
            ),
        )

    def connection_string(
        self,
        user: str,
        password: str,
        *,
        database: str | None = None,
        extra: str = "",
    ) -> str:
        suffix = f";{extra.strip(';')}" if extra else ""
        return (
            f"Server={self.host},{self.port};"
            f"Database={database or self.database};"
            f"User Id={user};Password={password};"
            f"Encrypt=True;TrustServerCertificate=True{suffix}"
        )

    def __repr__(self) -> str:
        return (
            "SqlAuthConfig("
            f"host={self.host!r}, port={self.port!r}, "
            f"database={self.database!r}, "
            f"upstream_database={self.upstream_database!r}, "
            f"owner_user={self.owner_user!r}, "
            f"readonly_user={self.readonly_user!r}, "
            f"denied_user={self.denied_user!r})"
        )
