from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ChatbotRespuesta, ChatbotInteraccion
from ..schemas import (
    ChatbotResponse,
    ChatbotResponseCreate,
    ChatbotResponseUpdate,
    ChatbotInteractionCreate,
    ChatbotInteractionResponse,
)
from ..security import require_role, get_current_user_optional

router = APIRouter(prefix="/chatbot", tags=["Chatbot"])


@router.get("/responses", response_model=List[ChatbotResponse])
async def get_chatbot_responses(
    active: bool = True,
    db: Session = Depends(get_db),
):
    query = db.query(ChatbotRespuesta)
    if active:
        query = query.filter(ChatbotRespuesta.activa.is_(True))
    responses = query.order_by(ChatbotRespuesta.orden.asc().nulls_last(), ChatbotRespuesta.created_at.asc()).all()
    return responses


@router.post("/responses", response_model=ChatbotResponse, status_code=status.HTTP_201_CREATED)
async def create_chatbot_response(
    payload: ChatbotResponseCreate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    existing = db.query(ChatbotRespuesta).filter(ChatbotRespuesta.clave == payload.clave).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una respuesta con esa clave.",
        )

    chatbot_response = ChatbotRespuesta(
        clave=payload.clave,
        texto=payload.texto,
        categoria=payload.categoria,
        activa=payload.activa,
        orden=payload.orden,
        id_creador=current_user.get("id"),
    )
    db.add(chatbot_response)
    db.commit()
    db.refresh(chatbot_response)
    return chatbot_response


@router.put("/responses/{response_id}", response_model=ChatbotResponse)
async def update_chatbot_response(
    response_id: UUID,
    payload: ChatbotResponseUpdate,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    chatbot_response = db.query(ChatbotRespuesta).filter(ChatbotRespuesta.id_respuesta == response_id).first()
    if not chatbot_response:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta de chatbot no encontrada.")

    if payload.clave is not None:
        chatbot_response.clave = payload.clave
    if payload.texto is not None:
        chatbot_response.texto = payload.texto
    if payload.categoria is not None:
        chatbot_response.categoria = payload.categoria
    if payload.activa is not None:
        chatbot_response.activa = payload.activa
    if payload.orden is not None:
        chatbot_response.orden = payload.orden

    db.add(chatbot_response)
    db.commit()
    db.refresh(chatbot_response)
    return chatbot_response


@router.delete("/responses/{response_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chatbot_response(
    response_id: UUID,
    current_user: dict = Depends(require_role(["admin", "psicologo"])),
    db: Session = Depends(get_db),
):
    chatbot_response = db.query(ChatbotRespuesta).filter(ChatbotRespuesta.id_respuesta == response_id).first()
    if not chatbot_response:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta de chatbot no encontrada.")

    chatbot_response.activa = False
    db.add(chatbot_response)
    db.commit()
    return


@router.post("/interactions", response_model=ChatbotInteractionResponse, status_code=status.HTTP_201_CREATED)
async def register_chatbot_interaction(
    payload: ChatbotInteractionCreate,
    current_user: Optional[dict] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    Registra una interacción del chatbot (HU0016). Cada vez que el frontend
    obtiene una respuesta para el usuario, reporta si hubo match (resuelta)
    o cayó en fallback off-topic, para poder calcular métricas de desempeño.
    """
    interaction = ChatbotInteraccion(
        pregunta=payload.pregunta,
        resuelta=payload.resuelta,
        clave_respuesta=payload.clave_respuesta,
        id_usuario=UUID(current_user["id"]) if current_user else None,
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)
    return interaction
