from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

_engine_kwargs: dict = {
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10,
    "pool_recycle": 300,
    "pool_timeout": 30,
}

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)


def check_database_connection() -> tuple[bool, str]:
    """Verifica conexión a la base de datos (Supabase Postgres)."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        if settings.is_supabase_postgres:
            return True, f"Conectado a Supabase PostgreSQL ({settings.SUPABASE_PROJECT_REF or 'proyecto'})"
        return True, "Conectado a PostgreSQL"
    except Exception as e:
        return False, str(e)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
