from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from uuid import UUID
from ..database import get_db
from ..models import BackupConfig, BackupLog
from ..schemas import (
    BackupConfigResponse,
    BackupConfigUpdate,
    BackupLogResponse,
    BackupRunResponse,
    BackupRestoreResponse,
)
from ..security import require_role
from ..utils.backup import create_db_backup, restore_db_backup
import os

router = APIRouter(prefix="/admin/backups", tags=["Backup & Restore (HU0032)"])


# --- GET /admin/backups/config ---
@router.get("/config", response_model=BackupConfigResponse)
async def get_backup_config(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Obtiene la configuración activa de copias de seguridad automáticas (HU0032 CA1).
    Si no existe configuración, crea una por defecto (manual, inactiva).
    """
    config = db.query(BackupConfig).first()
    if config is None:
        config = BackupConfig(
            periodicidad="manual",
            hora="02:00",
            activo=False,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


# --- PUT /admin/backups/config ---
@router.put("/config", response_model=BackupConfigResponse)
async def update_backup_config(
    payload: BackupConfigUpdate,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Actualiza la política de respaldos automáticos (HU0032 CA1).
    Permite definir periodicidad (diaria, semanal, mensual, manual),
    hora de ejecución, y día correspondiente.
    """
    config = db.query(BackupConfig).first()
    if config is None:
        config = BackupConfig(
            periodicidad="manual",
            hora="02:00",
            activo=False,
        )
        db.add(config)
        db.commit()
        db.refresh(config)

    if payload.periodicidad is not None:
        valid_options = ("diaria", "semanal", "mensual", "manual")
        if payload.periodicidad not in valid_options:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Periodicidad inválida. Opciones: {', '.join(valid_options)}",
            )
        config.periodicidad = payload.periodicidad
    if payload.hora is not None:
        # Validar formato HH:MM
        try:
            parts = payload.hora.split(":")
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Formato de hora inválido. Usar HH:MM (e.g. 02:30)",
            )
        config.hora = payload.hora
    if payload.dia_semana is not None:
        config.dia_semana = payload.dia_semana
    if payload.dia_mes is not None:
        config.dia_mes = payload.dia_mes
    if payload.activo is not None:
        config.activo = payload.activo

    db.add(config)
    db.commit()
    db.refresh(config)

    # Reload the scheduler to apply new scheduling policy (HU0032 CA1)
    try:
        from ..main import _setup_backup_scheduler
        _setup_backup_scheduler()
    except Exception:
        pass  # Scheduler refresh is best-effort

    return config


# --- GET /admin/backups ---
@router.get("", response_model=list[BackupLogResponse])
async def list_backups(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Lista el historial de copias de seguridad ordenado por fecha descendente (HU0032 CA3).
    """
    logs = (
        db.query(BackupLog)
        .order_by(BackupLog.fecha_creacion.desc())
        .limit(50)
        .all()
    )
    return logs


# --- POST /admin/backups/run ---
@router.post("/run", response_model=BackupRunResponse, status_code=status.HTTP_201_CREATED)
async def run_backup(
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Ejecuta una copia de seguridad manual inmediata (HU0032 CA1 & CA2).
    """
    try:
        log_entry = create_db_backup(db, tipo="manual")
        if log_entry.estado == "completado":
            return BackupRunResponse(
                success=True,
                message=f"Respaldo '{log_entry.nombre}' creado exitosamente.",
                backup=BackupLogResponse.model_validate(log_entry),
            )
        else:
            return BackupRunResponse(
                success=False,
                message=f"El respaldo falló: {log_entry.error_mensaje}",
                backup=BackupLogResponse.model_validate(log_entry),
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al ejecutar el respaldo: {str(e)}",
        )


# --- POST /admin/backups/{backup_id}/restore ---
@router.post("/{backup_id}/restore", response_model=BackupRestoreResponse)
async def restore_backup(
    backup_id: UUID,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Restaura la base de datos de forma segura a un punto de restauración (HU0032 CA3).
    Automáticamente crea un respaldo preventivo antes de restaurar.
    """
    backup_log = db.query(BackupLog).filter(BackupLog.id_backup == backup_id).first()
    if backup_log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Punto de restauración no encontrado.",
        )
    if backup_log.estado != "completado":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede restaurar desde un respaldo fallido.",
        )

    try:
        restore_db_backup(db, backup_id)
        return BackupRestoreResponse(
            success=True,
            message=f"Base de datos restaurada exitosamente al punto '{backup_log.nombre}'.",
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante la restauración: {str(e)}",
        )


# --- GET /admin/backups/{backup_id}/download ---
@router.get("/{backup_id}/download")
async def download_backup(
    backup_id: UUID,
    current_user: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """
    Descarga el archivo de respaldo físico (HU0032 CA3).
    """
    backup_log = db.query(BackupLog).filter(BackupLog.id_backup == backup_id).first()
    if backup_log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Respaldo no encontrado.",
        )
    if not os.path.exists(backup_log.ruta):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El archivo de respaldo no existe en el sistema de archivos.",
        )
    return FileResponse(
        path=backup_log.ruta,
        filename=backup_log.nombre,
        media_type="application/json",
    )
