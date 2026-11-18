"""Consultas compartidas para evitar N+1 en los endpoints del panel."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Respuesta


def phq9_scores_subquery(db: Session):
    """
    Subconsulta (id_evaluacion, puntaje) con la suma PHQ-9 de cada evaluación.

    Se une con LEFT JOIN a la consulta principal en lugar de ejecutar un
    SELECT SUM(...) por cada fila del listado.
    """
    return (
        db.query(
            Respuesta.id_evaluacion.label("id_evaluacion"),
            func.sum(Respuesta.valor).label("puntaje"),
        )
        .group_by(Respuesta.id_evaluacion)
        .subquery()
    )


def phq9_scores_for(db: Session, eval_ids) -> dict:
    """Puntaje PHQ-9 de varias evaluaciones en una sola consulta: {id_evaluacion: puntaje}."""
    eval_ids = list(eval_ids)
    if not eval_ids:
        return {}
    rows = (
        db.query(Respuesta.id_evaluacion, func.sum(Respuesta.valor))
        .filter(Respuesta.id_evaluacion.in_(eval_ids))
        .group_by(Respuesta.id_evaluacion)
        .all()
    )
    return {eval_id: int(total or 0) for eval_id, total in rows}
