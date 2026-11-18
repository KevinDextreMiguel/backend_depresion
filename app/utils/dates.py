"""
Fechas de los filtros del panel interpretadas en la hora local de la institución.

La base guarda `timestamptz` en UTC. Si "16/11/2026" se interpreta como
medianoche UTC, las evaluaciones hechas en Lima después de las 19:00 de ese día
(00:00 UTC del día siguiente) quedan fuera del rango.
"""

import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Lima")


def _load_tz() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(APP_TIMEZONE)
    except Exception:
        # Windows sin el paquete tzdata: Perú usa UTC-5 todo el año (sin horario de verano).
        return timezone(timedelta(hours=-5), "PET")


LOCAL_TZ = _load_tz()


def _parse_day(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


def local_day_start(value: str) -> datetime:
    """Inicio (00:00 hora local) del día YYYY-MM-DD, como datetime con zona horaria."""
    return datetime.combine(_parse_day(value), time.min, tzinfo=LOCAL_TZ)


def local_day_end_exclusive(value: str) -> datetime:
    """00:00 hora local del día siguiente: límite superior exclusivo para "Hasta"."""
    return local_day_start(value) + timedelta(days=1)
