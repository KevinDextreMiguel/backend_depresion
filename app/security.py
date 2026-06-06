import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import settings
from sqlalchemy import func
from cryptography.fernet import Fernet
import base64

security_bearer = HTTPBearer()

_supabase_client = None


def get_supabase_client():
    """Cliente Supabase solo si hay URL válida configurada."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        return None
    if "supabase.co" not in settings.SUPABASE_URL:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        return _supabase_client
    except Exception:
        return None


# Local backup encryption
try:
    raw_key = settings.ENCRYPTION_KEY.encode()
    if len(raw_key) < 32:
        raw_key = raw_key.ljust(32, b"0")
    else:
        raw_key = raw_key[:32]
    fernet_key = base64.urlsafe_b64encode(raw_key)
    cipher_suite = Fernet(fernet_key)
except Exception:
    cipher_suite = None


def encrypt_local(text: str) -> bytes:
    if not text:
        return b""
    if cipher_suite:
        return cipher_suite.encrypt(text.encode())
    return text.encode()


def decrypt_local(encrypted_bytes: bytes) -> str:
    if not encrypted_bytes:
        return ""
    if cipher_suite:
        try:
            return cipher_suite.decrypt(encrypted_bytes).decode()
        except Exception:
            return encrypted_bytes.decode()
    return encrypted_bytes.decode()


def db_encrypt(plain_text: str):
    return func.encrypt_sensible_data(plain_text, settings.ENCRYPTION_KEY)


def db_decrypt(column_expr):
    return func.decrypt_sensible_data(column_expr, settings.ENCRYPTION_KEY)


def _user_from_jwt(decoded: dict) -> dict:
    role = decoded.get("role") or decoded.get("user_metadata", {}).get("role")
    return {
        "id": decoded.get("sub"),
        "email": decoded.get("email"),
        "role": role,
        "metadata": {"role": role},
    }


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
) -> dict:
    token = credentials.credentials

    # 1. JWT local (modo por defecto)
    if settings.JWT_SECRET:
        try:
            decoded = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
            return _user_from_jwt(decoded)
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="El token ha expirado")
        except jwt.InvalidTokenError:
            pass

    # 2. Supabase Auth (solo si está configurado y disponible)
    supabase = get_supabase_client()
    if supabase:
        try:
            user_response = supabase.auth.get_user(token)
            if user_response and user_response.user:
                meta = user_response.user.user_metadata or {}
                return {
                    "id": user_response.user.id,
                    "email": user_response.user.email,
                    "metadata": meta,
                    "role": meta.get("role") or user_response.user.role,
                }
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail=f"Autenticación fallida: {str(e)}",
            )

    raise HTTPException(status_code=401, detail="Token de acceso inválido o expirado")


def require_role(roles_allowed: list[str]):
    async def role_checker(current_user: dict = Depends(get_current_user)) -> dict:
        user_role = current_user.get("metadata", {}).get("role") or current_user.get("role")
        if user_role not in roles_allowed and "authenticated" not in roles_allowed:
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado: permisos insuficientes para esta operación",
            )
        return current_user

    return role_checker
