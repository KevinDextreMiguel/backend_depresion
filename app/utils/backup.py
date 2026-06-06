import os
import json
import base64
import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import Base, engine
from ..models import BackupLog, Notificacion

# Directorio de respaldos relativo al backend
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS_DIR = os.path.join(BACKEND_DIR, "backups")

class BackupEncoder(json.JSONEncoder):
    """Codificador JSON personalizado para tipos de datos de base de datos."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, bytes):
            return base64.b64encode(obj).decode("utf-8")
        return super().default(obj)


def create_db_backup(db: Session, tipo: str = "manual") -> BackupLog:
    """
    Genera un respaldo lógico de todas las tablas registradas en SQLAlchemy,
    guardando el resultado en un archivo JSON serializado.
    """
    if not os.path.exists(BACKUPS_DIR):
        os.makedirs(BACKUPS_DIR)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{tipo}_{timestamp}.json"
    filepath = os.path.join(BACKUPS_DIR, filename)

    try:
        backup_data = {}

        # Ordenar tablas de forma topológica para asegurar la integridad referencial
        for table in Base.metadata.sorted_tables:
            # Evitar respaldar las tablas de auditoría/logs de backup para no crecer recursivamente
            if table.name in ("backup_log", "backup_config"):
                continue

            # Obtener todas las filas de la tabla
            rows = db.execute(table.select()).all()
            backup_data[table.name] = [dict(row._mapping) for row in rows]

        # Guardar en archivo JSON
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, cls=BackupEncoder, indent=2)

        # Medir tamaño del archivo
        size_bytes = os.path.getsize(filepath)

        # Registrar en el log de respaldos
        log_entry = BackupLog(
            id_backup=uuid.uuid4(),
            nombre=filename,
            ruta=filepath,
            tamano_bytes=size_bytes,
            estado="completado",
            tipo=tipo
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)

        # Crear una notificación para el administrador
        notif = Notificacion(
            id_notificacion=uuid.uuid4(),
            titulo="Copia de Seguridad Completada",
            mensaje=f"Se completó exitosamente la copia de seguridad '{filename}' ({round(size_bytes / 1024, 1)} KB).",
            nivel_riesgo="minimo",
            alerta_suicidio=False,
            leida=False,
            revisada=False
        )
        db.add(notif)
        db.commit()

        return log_entry

    except Exception as e:
        db.rollback()
        error_msg = str(e)
        print(f"[Backup Error] {error_msg}")

        # Intentar registrar el fallo en el log
        try:
            log_entry = BackupLog(
                id_backup=uuid.uuid4(),
                nombre=filename,
                ruta=filepath,
                tamano_bytes=0,
                estado="fallido",
                tipo=tipo,
                error_mensaje=error_msg
            )
            db.add(log_entry)
            db.commit()

            # Crear notificación de fallo
            notif = Notificacion(
                id_notificacion=uuid.uuid4(),
                titulo="Copia de Seguridad Fallida",
                mensaje=f"Error al generar la copia de seguridad '{filename}': {error_msg}",
                nivel_riesgo="leve",
                alerta_suicidio=False,
                leida=False,
                revisada=False
            )
            db.add(notif)
            db.commit()
            return log_entry
        except Exception as inner_e:
            db.rollback()
            print(f"[Backup Inner Error] {inner_e}")
            raise e


def restore_db_backup(db: Session, id_backup: uuid.UUID) -> bool:
    """
    Restaura la base de datos de forma segura dentro de una transacción.
    Primero realiza un backup preventivo.
    """
    backup_log = db.query(BackupLog).filter(BackupLog.id_backup == id_backup).first()
    if not backup_log or backup_log.estado != "completado":
        raise ValueError("Punto de restauración no encontrado o inválido.")

    if not os.path.exists(backup_log.ruta):
        raise FileNotFoundError(f"El archivo de respaldo no existe físicamente: {backup_log.ruta}")

    # 1. Crear copia de seguridad preventiva automática antes de restaurar
    create_db_backup(db, tipo="backup_pre_restore")

    try:
        # Cargar datos del respaldo
        with open(backup_log.ruta, "r", encoding="utf-8") as f:
            backup_data = json.load(f)

        # Iniciar transacción controlada
        with db.begin_nested():
            # 2. Borrar datos en orden inverso de dependencias para respetar claves foráneas
            for table in reversed(Base.metadata.sorted_tables):
                if table.name in ("backup_log", "backup_config"):
                    continue
                db.execute(table.delete())

            # 3. Insertar datos en orden directo de dependencias
            for table in Base.metadata.sorted_tables:
                if table.name in ("backup_log", "backup_config"):
                    continue

                table_rows = backup_data.get(table.name, [])
                if not table_rows:
                    continue

                converted_rows = []
                for row in table_rows:
                    converted_row = {}
                    for col in table.columns:
                        val = row.get(col.name)
                        if val is None:
                            converted_row[col.name] = None
                            continue

                        # Convertir tipos serializados de vuelta a tipos SQLAlchemy/Python
                        col_type_str = str(col.type).lower()

                        if "uuid" in col_type_str:
                            converted_row[col.name] = uuid.UUID(val)
                        elif "timestamp" in col_type_str or "datetime" in col_type_str:
                            converted_row[col.name] = datetime.fromisoformat(val)
                        elif "date" in col_type_str:
                            converted_row[col.name] = date.fromisoformat(val)
                        elif "numeric" in col_type_str or "decimal" in col_type_str:
                            converted_row[col.name] = Decimal(str(val))
                        elif "binary" in col_type_str or "bytea" in col_type_str or "largebinary" in col_type_str:
                            converted_row[col.name] = base64.b64decode(val.encode("utf-8"))
                        else:
                            converted_row[col.name] = val
                    converted_rows.append(converted_row)

                if converted_rows:
                    db.execute(table.insert(), converted_rows)

        # Confirmar la transacción
        db.commit()

        # Crear notificación de restauración exitosa
        notif = Notificacion(
            id_notificacion=uuid.uuid4(),
            titulo="Restauración Completada",
            mensaje=f"La base de datos se restauró exitosamente al punto de restauración '{backup_log.nombre}'.",
            nivel_riesgo="minimo",
            alerta_suicidio=False,
            leida=False,
            revisada=False
        )
        db.add(notif)
        db.commit()

        return True

    except Exception as e:
        db.rollback()
        print(f"[Restore Error] {e}")
        # Crear notificación de restauración fallida
        try:
            notif = Notificacion(
                id_notificacion=uuid.uuid4(),
                titulo="Restauración Fallida",
                mensaje=f"Error al restaurar al punto '{backup_log.nombre}': {str(e)}",
                nivel_riesgo="leve",
                alerta_suicidio=False,
                leida=False,
                revisada=False
            )
            db.add(notif)
            db.commit()
        except Exception:
            pass
        raise e
