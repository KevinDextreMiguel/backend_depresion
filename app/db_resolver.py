"""
Resuelve una URL de base de datos que funcione en este equipo.
Supabase directo (db.*.supabase.co) suele ser solo IPv6; en Windows sin IPv6
hay que usar el Session pooler (IPv4) desde el Dashboard de Supabase.
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text


def _test_url(url: str, timeout: int = 8) -> tuple[bool, str]:
    try:
        kw: dict = {
            "pool_pre_ping": True,
            "pool_size": 3,
            "max_overflow": 5,
        }
        engine = create_engine(url, **kw)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True, ""
    except Exception as e:
        return False, str(e)


def _candidate_urls() -> list[tuple[str, str]]:
    """Lista (etiqueta, url) en orden de prioridad."""
    out: list[tuple[str, str]] = []

    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        out.append(("DATABASE_URL", explicit))

    pooler = os.getenv("SUPABASE_POOLER_URL", "").strip()
    if pooler:
        out.append(("SUPABASE_POOLER_URL", pooler))

    password = os.getenv("SUPABASE_DB_PASSWORD", "").strip()
    project_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()
    if not project_ref:
        url = os.getenv("SUPABASE_URL", "")
        if "supabase.co" in url:
            project_ref = url.replace("https://", "").split(".")[0]

    if password and project_ref:
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

    return out


def resolve_database_url(verbose: bool = True) -> str:
    errors = []
    candidates = _candidate_urls()
    if not candidates:
        raise RuntimeError(
            "No se encontraron candidatos de conexión. Asegúrate de configurar DATABASE_URL, "
            "SUPABASE_POOLER_URL o la combinación de SUPABASE_DB_PASSWORD y SUPABASE_PROJECT_REF en tus variables de entorno."
        )

    for label, url in candidates:
        # Ocultar contraseña en logs
        safe_url = url
        if "@" in url:
            parts = url.split("@")
            prefix = parts[0].split(":")
            if len(prefix) > 2:
                safe_url = f"{prefix[0]}:{prefix[1]}:***@{parts[1]}"
        
        ok, err = _test_url(url)
        if ok:
            if verbose:
                print(f"[MindCheck] Conexión OK ({label})")
            return url
        else:
            err_msg = f"{label}: {err} (URL: {safe_url})"
            errors.append(err_msg)
            if verbose:
                print(f"[MindCheck] Falló conexión ({label}): {err}")

    details = "\n".join(errors)
    raise RuntimeError(
        f"Ninguna conexión a la base de datos de Supabase funcionó.\nDetalles de los intentos:\n{details}"
    )
