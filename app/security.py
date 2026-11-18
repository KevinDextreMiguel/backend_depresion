import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
from .config import settings
from sqlalchemy import func
from cryptography.fernet import Fernet
import base64
import hashlib
import threading
import time

security_bearer = HTTPBearer()
security_bearer_optional = HTTPBearer(auto_error=False)

_supabase_client = None
_supabase_anon_client = None


def get_supabase_client():
    """Cliente Supabase con service_role key — necesario para auth.admin.* operations."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_KEY
    if not url or not key:
        return None
    if "supabase.co" not in url:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(url, key)
        return _supabase_client
    except Exception as e:
        print(f"[Security] Failed to create service client: {e}", flush=True)
        return None


def get_supabase_anon_client():
    """Cliente Supabase con anon/publishable key — necesario para sign_in_with_password y sign_up."""
    global _supabase_anon_client
    if _supabase_anon_client is not None:
        return _supabase_anon_client
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_ANON_KEY
    if not url or not key:
        # fallback: si no hay anon key, usar service key como cliente
        return get_supabase_client()
    if "supabase.co" not in url:
        return None
    try:
        from supabase import create_client
        _supabase_anon_client = create_client(url, key)
        return _supabase_anon_client
    except Exception as e:
        print(f"[Security] Failed to create anon client: {e}", flush=True)
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


# Caché corta de tokens validados contra Supabase Auth. Sin ella, cada petición
# del panel hacía una llamada HTTP a Supabase antes de tocar la base de datos.
_SUPABASE_USER_CACHE: dict[str, tuple[float, dict]] = {}
_SUPABASE_USER_CACHE_LOCK = threading.Lock()
_SUPABASE_USER_CACHE_TTL = 60  # segundos
_SUPABASE_USER_CACHE_MAX = 1000


def _token_cache_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cache_get_user(token: str) -> dict | None:
    key = _token_cache_key(token)
    now = time.time()
    with _SUPABASE_USER_CACHE_LOCK:
        entry = _SUPABASE_USER_CACHE.get(key)
        if entry and entry[0] > now:
            return entry[1]
        if entry:
            _SUPABASE_USER_CACHE.pop(key, None)
    return None


def _cache_put_user(token: str, user: dict) -> None:
    expires_at = time.time() + _SUPABASE_USER_CACHE_TTL
    # Nunca cachear más allá de la expiración del propio token.
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
        if exp:
            expires_at = min(expires_at, float(exp))
    except jwt.PyJWTError:
        pass
    with _SUPABASE_USER_CACHE_LOCK:
        if len(_SUPABASE_USER_CACHE) >= _SUPABASE_USER_CACHE_MAX:
            now = time.time()
            for k in [k for k, (exp_at, _) in _SUPABASE_USER_CACHE.items() if exp_at <= now]:
                _SUPABASE_USER_CACHE.pop(k, None)
            if len(_SUPABASE_USER_CACHE) >= _SUPABASE_USER_CACHE_MAX:
                _SUPABASE_USER_CACHE.clear()
        _SUPABASE_USER_CACHE[_token_cache_key(token)] = (expires_at, user)


_jwks_client = None
_jwks_lock = threading.Lock()


def _verify_supabase_jwt_locally(token: str) -> dict | None:
    """
    Verifica localmente un token de Supabase Auth firmado con clave asimétrica
    (ES256/RS256) usando la clave pública del proyecto (JWKS, cacheada 1 h).
    No hace una llamada HTTP por petición como auth.get_user(). Devuelve None si
    el token no es de este tipo, para que se use el método de respaldo.
    Lanza jwt.ExpiredSignatureError si el token expiró.
    """
    global _jwks_client
    base = (settings.SUPABASE_URL or "").rstrip("/")
    if not base:
        return None
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    alg = header.get("alg")
    if alg not in ("ES256", "RS256") or not header.get("kid"):
        return None
    with _jwks_lock:
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token).key
        decoded = jwt.decode(
            token, signing_key, algorithms=[alg], audience="authenticated", issuer=f"{base}/auth/v1",
        )
    except jwt.ExpiredSignatureError:
        raise
    except Exception:
        return None
    meta = decoded.get("user_metadata") or {}
    return {
        "id": decoded.get("sub"),
        "email": decoded.get("email"),
        "metadata": meta,
        "role": meta.get("role") or decoded.get("role"),
    }


def _verify_with_supabase(supabase, token: str) -> dict | None:
    """Llamada HTTP bloqueante a Supabase Auth; se ejecuta en el threadpool."""
    user_response = supabase.auth.get_user(token)
    if user_response and user_response.user:
        meta = user_response.user.user_metadata or {}
        return {
            "id": user_response.user.id,
            "email": user_response.user.email,
            "metadata": meta,
            "role": meta.get("role") or user_response.user.role,
        }
    return None


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

    # 2. Token de Supabase Auth verificado localmente con la clave pública (JWKS)
    try:
        local_user = await run_in_threadpool(_verify_supabase_jwt_locally, token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="El token ha expirado")
    if local_user is not None:
        return local_user

    # 3. Respaldo: Supabase Auth por HTTP (con caché), p. ej. tokens HS256 antiguos
    supabase = get_supabase_client()
    if supabase:
        cached = _cache_get_user(token)
        if cached is not None:
            return cached
        try:
            user = await run_in_threadpool(_verify_with_supabase, supabase, token)
            if user is not None:
                _cache_put_user(token, user)
                return user
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail=f"Autenticación fallida: {str(e)}",
            )

    raise HTTPException(status_code=401, detail="Token de acceso inválido o expirado")


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security_bearer_optional),
) -> dict | None:
    """
    Igual que get_current_user, pero no exige que el request traiga un Bearer token.
    Usado en endpoints que deben seguir aceptando flujos anónimos (ej. progreso de
    cuestionario antes de iniciar sesión) pero que, si el llamante SÍ envía un token,
    deben validarlo igual que cualquier ruta autenticada — evita que un token ausente
    se use como forma de eludir la verificación de propiedad de un recurso.
    """
    if credentials is None:
        return None
    return await get_current_user(credentials)


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
