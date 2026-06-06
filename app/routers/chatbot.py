from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ChatbotRespuesta
from ..schemas import ChatbotResponse, ChatbotResponseCreate, ChatbotResponseUpdate
from ..security import require_role

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
