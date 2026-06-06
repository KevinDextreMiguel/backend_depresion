import os
from urllib.parse import quote_plus
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


def _extract_project_ref(supabase_url: str) -> str:
    """Extrae el ref del proyecto desde https://xxxx.supabase.co"""
    if not supabase_url:
        return ""
    host = supabase_url.replace("https://", "").replace("http://", "").split("/")[0]
    return host.replace(".supabase.co", "").strip()


def build_database_url() -> str:
    """
    Si AUTO_RESOLVE_DB=true (por defecto), prueba pooler/direct/SQLite al arrancar.
  Si no, construye URL directa o SQLite sin probar conexión.
    """
    if os.getenv("AUTO_RESOLVE_DB", "true").lower() in ("1", "true", "yes"):
        from .db_resolver import resolve_database_url

        return resolve_database_url(verbose=True)

    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return explicit

    pooler_url = os.getenv("SUPABASE_POOLER_URL", "").strip()
    if pooler_url:
        return pooler_url

    use_supabase = os.getenv("USE_SUPABASE_DB", "true").lower() in ("1", "true", "yes")
    password = os.getenv("SUPABASE_DB_PASSWORD", "").strip()
    project_ref = os.getenv(
        "SUPABASE_PROJECT_REF",
        _extract_project_ref(os.getenv("SUPABASE_URL", "")),
    )

    if use_supabase and password and project_ref:
        encoded_password = quote_plus(password)
        return (
            f"postgresql://postgres:{encoded_password}"
            f"@db.{project_ref}.supabase.co:5432/postgres?sslmode=require"
        )

    return "sqlite:///./mindcheck_local.db"


class Settings(BaseSettings):
    PROJECT_NAME: str = "Salud Mental API"
    VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    CORS_ORIGINS: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )

    USE_SUPABASE_DB: bool = os.getenv("USE_SUPABASE_DB", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    SUPABASE_PROJECT_REF: str = os.getenv("SUPABASE_PROJECT_REF", "")

    DATABASE_URL: str = build_database_url()

    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    SUPABASE_DB_PASSWORD: str = os.getenv("SUPABASE_DB_PASSWORD", "")

    ENCRYPTION_KEY: str = os.getenv(
        "ENCRYPTION_KEY", "super-secret-key-depression-screening-app-2026"
    )
    JWT_SECRET: str = os.getenv(
        "JWT_SECRET",
        "dev-mindcheck-jwt-secret-cambiar-en-produccion",
    )
    JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "24"))
    # local = JWT en FastAPI (tablas en Supabase Postgres); supabase = Supabase Auth
    AUTH_MODE: str = os.getenv("AUTH_MODE", "local")

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_supabase_postgres(self) -> bool:
        return "supabase.co" in self.DATABASE_URL

    class Config:
        case_sensitive = True


settings = Settings()
