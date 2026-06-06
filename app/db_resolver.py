"""
Resuelve una URL de base de datos que funcione en este equipo.
Supabase directo (db.*.supabase.co) suele ser solo IPv6; en Windows sin IPv6
hay que usar el Session pooler (IPv4) desde el Dashboard de Supabase.
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text


def _test_url(url: str, timeout: int = 8) -> bool:
    try:
        kw: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, **kw)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _candidate_urls() -> list[tuple[str, str]]:
    """Lista (etiqueta, url) en orden de prioridad."""
    out: list[tuple[str, str]] = []

    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        out.append(("DATABASE_URL", explicit))

    pooler = os.getenv("SUPABASE_POOLER_URL", "").strip()
    if pooler:
        out.append(("SUPABASE_POOLER_URL", pooler))

    use_supabase = os.getenv("USE_SUPABASE_DB", "true").lower() in ("1", "true", "yes")
    password = os.getenv("SUPABASE_DB_PASSWORD", "").strip()
    project_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()
    if not project_ref:
        url = os.getenv("SUPABASE_URL", "")
        if "supabase.co" in url:
            project_ref = url.replace("https://", "").split(".")[0]

    if use_supabase and password and project_ref:
        enc = quote_plus(password)

        region = os.getenv("SUPABASE_REGION", "").strip()
        regions = [region] if region else []
        regions += [
            "sa-east-1",
            "us-east-1",
            "us-west-1",
            "us-west-2",
            "eu-west-1",
            "eu-central-1",
            "ap-southeast-1",
        ]
        seen: set[str] = set()
        for reg in regions:
            if not reg or reg in seen:
                continue
            seen.add(reg)
            for prefix in ("aws-0", "aws-1"):
                pool = (
                    f"postgresql://postgres.{project_ref}:{enc}"
                    f"@{prefix}-{reg}.pooler.supabase.com:5432/postgres"
                    f"?sslmode=require"
                )
                out.append((f"pooler-{prefix}-{reg}", pool))

        direct = (
            f"postgresql://postgres:{enc}"
            f"@db.{project_ref}.supabase.co:5432/postgres?sslmode=require"
        )
        out.append(("direct-ipv6", direct))

    out.append(("sqlite-local", "sqlite:///./mindcheck_local.db"))
    return out


def resolve_database_url(verbose: bool = True) -> str:
    for label, url in _candidate_urls():
        if _test_url(url):
            if verbose:
                if url.startswith("sqlite"):
                    print(
                        "[MindCheck] Usando SQLite local. Para Supabase, define "
                        "SUPABASE_POOLER_URL en .env (Dashboard -> Connect -> ORMs -> Session)."
                    )
                else:
                    print(f"[MindCheck] Conexión OK ({label})")
            return url

    if verbose:
        print("[MindCheck] Ninguna conexión Supabase funcionó; usando SQLite local.")
    return "sqlite:///./mindcheck_local.db"
