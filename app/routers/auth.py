from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..database import get_db
from ..models import Usuario, Administrador, Psicologo, Estudiante, AuditoriaAcceso
from ..schemas import UserCreate, UserResponse, StudentSignup, AuthTokenResponse, UserLogin, ForgotPasswordRequest, ResetPasswordRequest, UserUpdateProfile
from ..security import get_supabase_client, get_supabase_anon_client, get_current_user
import uuid
import traceback

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=AuthTokenResponse)
async def login(credentials: UserLogin, db: Session = Depends(get_db)):
    """Inicio de sesión con Supabase Auth."""
    # Use anon client for sign_in_with_password (requires publishable/anon key)
    anon_client = get_supabase_anon_client()
    service_client = get_supabase_client()
    supabase_client = anon_client or service_client
    if not supabase_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de Supabase no configurado."
        )
    
    try:
        response = supabase_client.auth.sign_in_with_password({
            "email": credentials.email,
            "password": credentials.password
        })
        
        if not response or not response.session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Correo o contraseña incorrectos."
            )
            
        uid = uuid.UUID(response.user.id)
        
        # Validar si el usuario existe en nuestra DB y está activo
        usuario = db.query(Usuario).filter(Usuario.id_usuario == uid).first()
        if not usuario or not usuario.activo:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cuenta desactivada o no encontrada en base de datos local."
            )
            
        estudiante_data = None
        if usuario.rol == "estudiante" and usuario.estudiante:
            estudiante_data = {
                "edad": usuario.estudiante.edad,
                "genero": usuario.estudiante.genero,
                "carrera": usuario.estudiante.carrera,
                "universidad": usuario.estudiante.universidad
            }

        return AuthTokenResponse(
            access_token=response.session.access_token,
            user={
                "id": response.user.id,
                "email": credentials.email,
                "nombre": usuario.nombre,
                "foto_perfil": usuario.foto_perfil,
                "rol": usuario.rol,
                "estudiante": estudiante_data
            }
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"[Auth Login Error] Details: {e}", flush=True)
        err_msg = str(e).lower()
        if "invalid login credentials" in err_msg or "invalid password" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Correo o contraseña incorrectos."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en inicio de sesión: {str(e)}"
        )


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Solicitar recuperación de contraseña (HU0003)."""
    # 1. Verificar si el correo existe localmente
    usuario = db.query(Usuario).filter(Usuario.correo == request.email).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El correo electrónico no está registrado."
        )

    # 2. Llamar a Supabase para enviar correo de reseteo
    supabase_client = get_supabase_client()
    if not supabase_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de Supabase no configurado."
        )

    try:
        supabase_client.auth.reset_password_email(
            request.email,
            options={"redirect_to": "http://localhost:5173/"}
        )
        return {"detail": "Enlace de recuperación enviado exitosamente al correo."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al enviar correo de recuperación: {str(e)}"
        )


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(request: ResetPasswordRequest):
    """Actualizar contraseña tras hacer clic en enlace de recuperación (HU0003)."""
    supabase_client = get_supabase_client()
    if not supabase_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de Supabase no configurado."
        )

    try:
        # Usar el token del cliente para actualizar el usuario actual
        # Supabase Python Auth no tiene update_user que reciba token explícito en la librería estándar a veces.
        # En su lugar, inicializamos la sesión con el token, o usamos getUser.
        # Vamos a inicializar una instancia nueva o usar el supabase_client temporalmente logueado?
        # Supabase Python client.auth.get_user(request.access_token)
        user_resp = supabase_client.auth.get_user(request.access_token)
        if not user_resp or not user_resp.user:
             raise Exception("Token inválido o expirado")
        
        # Como supabase_client python a veces guarda estado global, usamos admin auth:
        supabase_client.auth.admin.update_user_by_id(
            user_resp.user.id,
            {"password": request.new_password}
        )
        return {"detail": "Contraseña actualizada exitosamente."}
    except Exception as e:
        err_msg = str(e).lower()
        if "expired" in err_msg or "invalid" in err_msg:
             raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El enlace ha expirado o es inválido. Solicita uno nuevo."
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al restablecer contraseña: {str(e)}"
        )


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(user_in: UserCreate, request: Request, db: Session = Depends(get_db)):
    """Registro usando Supabase Auth admin.create_user (auto-confirma email)."""
    service_client = get_supabase_client()
    if not service_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de Supabase no configurado."
        )

    supabase_uid = None
    try:
        # Use admin API: creates user as confirmed, satisfies FK constraint on usuario table
        auth_response = service_client.auth.admin.create_user({
            "email": user_in.email,
            "password": user_in.password,
            "email_confirm": True,
            "user_metadata": {
                "name": user_in.nombre,
                "role": user_in.rol
            }
        })
        
        if not auth_response or not auth_response.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudo registrar el usuario en Supabase Auth."
            )
            
        supabase_uid = uuid.UUID(auth_response.user.id)

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"[Auth Signup Admin Error] Exception: {e}", flush=True)
        print(f"[Auth Signup Admin Error] Traceback:\n{traceback.format_exc()}", flush=True)
        err_msg = str(e).lower()
        if "already" in err_msg or "exists" in err_msg or "in use" in err_msg or "registered" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo ya está en uso."
            )
        if "rate limit" in err_msg or "over_email_send_rate_limit" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Límite de registros alcanzado. Por favor intenta de nuevo en unos minutos."
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al registrar cuenta: {str(e)}"
        )

    # 3. Create profile in our custom `usuario` table using pg_sym_encrypt (pgcrypto)
    try:
        db_user = Usuario(
            id_usuario=supabase_uid,
            nombre=user_in.nombre,
            correo=user_in.email,
            rol=user_in.rol,
            activo=True
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

        # 4. Insert into role-specific tables
        if user_in.rol == "admin":
            db_admin = Administrador(id_usuario=supabase_uid, activo=True)
            db.add(db_admin)
        elif user_in.rol == "psicologo":
            db_psico = Psicologo(
                id_usuario=supabase_uid, 
                especialidad="Salud Mental General",
                numero_colegiatura=f"COP-{uuid.uuid4().hex[:6].upper()}",
                activo=True
            )
            db.add(db_psico)
        elif user_in.rol == "estudiante":
            db_stud = Estudiante(
                id_usuario=supabase_uid,
                edad=20,
                genero="Masculino",
                carrera="Ingeniería de Sistemas",
                universidad="UPC",
                activo=True
            )
            db.add(db_stud)
            
        db.commit()
        
        # 5. Log activity in access audit table
        client_ip = request.client.host if request.client else "127.0.0.1"
        db_audit = AuditoriaAcceso(
            id_usuario=supabase_uid,
            accion="escritura",
            tabla_objetivo="usuario",
            id_objetivo=supabase_uid,
            ip_origen=client_ip,
            detalle=f"Registro exitoso de usuario con rol {user_in.rol}"
        )
        db.add(db_audit)
        db.commit()

        # Retrieve the user record to return to frontend
        db_user = db.query(Usuario).filter(Usuario.id_usuario == supabase_uid).first()

        return db_user
    except Exception as e:
        # Rollback local DB on error
        db.rollback()
        # Clean up Supabase Auth user if DB insert failed to prevent orphaned records
        try:
            client = get_supabase_client()
            if client:
                client.auth.admin.delete_user(str(supabase_uid))
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al registrar perfil de usuario: {str(e)}"
        )


@router.get("/profile", response_model=UserResponse)
async def get_profile(
    current_user: dict = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Fetches the profile of the currently logged-in user, decrypting their name.
    """
    uid = uuid.UUID(current_user["id"])
    email = current_user.get("email", "")
    
    # Query user profile
    db_user = db.query(Usuario).filter(Usuario.id_usuario == uid).first()
    
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de usuario no encontrado en la base de datos."
        )
        
    return db_user


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: UserUpdateProfile,
    current_user: dict = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Updates the profile of the currently logged-in user.
    """
    uid = uuid.UUID(current_user["id"])
    
    db_user = db.query(Usuario).filter(Usuario.id_usuario == uid).first()
    
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de usuario no encontrado en la base de datos."
        )
        
    db_user.nombre = profile_data.nombre
    db_user.foto_perfil = profile_data.foto_perfil
    
    # Update estudiante demographic data if provided
    if db_user.rol == "estudiante" and db_user.estudiante:
        if profile_data.edad is not None:
            db_user.estudiante.edad = profile_data.edad
        if profile_data.genero is not None:
            db_user.estudiante.genero = profile_data.genero
        if profile_data.carrera is not None:
            db_user.estudiante.carrera = profile_data.carrera
        if profile_data.universidad is not None:
            db_user.estudiante.universidad = profile_data.universidad
            
    db.commit()
    db.refresh(db_user)
    
    try:
        supabase_client = get_supabase_client()
        if supabase_client:
            supabase_client.auth.admin.update_user_by_id(
                current_user["id"],
                {"user_metadata": {"name": profile_data.nombre}}
            )
    except Exception as e:
        print(f"Failed to sync name to Supabase: {e}")
        
    return db_user


@router.post("/signup-student", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
async def signup_student(
    student_in: StudentSignup, 
    request: Request, 
    db: Session = Depends(get_db)
):
    """
    Creates a new student account in Supabase Auth and registers them in our rel database schema.
    Uses auth.sign_up() (anon key) so it works regardless of service key format.
    """
    if not student_in.email or not student_in.password or not student_in.nombre or student_in.edad is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Todos los campos obligatorios deben ser completados."
        )

    if len(student_in.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña debe tener al menos 6 caracteres."
        )

    service_client = get_supabase_client()
    anon_client = get_supabase_anon_client()

    if not service_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registro requiere conexión a Supabase.",
        )

    supabase_uid = None
    # Use admin.create_user with email_confirm=True so the user is immediately
    # available in auth.users and satisfies the FK constraint on the usuario table
    try:
        auth_response = service_client.auth.admin.create_user({
            "email": student_in.email,
            "password": student_in.password,
            "email_confirm": True,
            "user_metadata": {
                "name": student_in.nombre,
                "role": "estudiante",
                "carrera": student_in.carrera,
                "universidad": student_in.universidad
            }
        })
        
        if not auth_response or not auth_response.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se pudo registrar el usuario en Supabase Auth."
            )
            
        supabase_uid = uuid.UUID(auth_response.user.id)

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"[Auth Signup Student Error] Exception: {e}", flush=True)
        print(f"[Auth Signup Student Error] Traceback:\n{traceback.format_exc()}", flush=True)
        err_msg = str(e).lower()
        if "already" in err_msg or "exists" in err_msg or "in use" in err_msg or "registered" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo ya está en uso."
            )
        if "rate limit" in err_msg or "over_email_send_rate_limit" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Límite de registros alcanzado. Por favor intenta de nuevo en unos minutos."
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error al registrar cuenta: {str(e)}"
        )

    # 3. Create profile in `usuario` using pgcrypto
    try:
        db_user = Usuario(
            id_usuario=supabase_uid,
            nombre=student_in.nombre,
            correo=student_in.email,
            rol="estudiante",
            activo=True
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

        # 4. Create profile in `estudiante`
        db_stud = Estudiante(
            id_usuario=supabase_uid,
            edad=student_in.edad,
            genero=student_in.genero,
            carrera=student_in.carrera,
            universidad=student_in.universidad,
            activo=True
        )
        db.add(db_stud)
        db.commit()

        # 5. Log activity
        client_ip = request.client.host if request.client else "127.0.0.1"
        db_audit = AuditoriaAcceso(
            id_usuario=supabase_uid,
            accion="escritura",
            tabla_objetivo="usuario",
            id_objetivo=supabase_uid,
            ip_origen=client_ip,
            detalle=f"Registro exitoso de estudiante: {student_in.nombre}"
        )
        db.add(db_audit)
        db.commit()

        # Sign in with password to get the access token and login the user immediately
        login_client = anon_client or service_client
        login_response = login_client.auth.sign_in_with_password({
            "email": student_in.email,
            "password": student_in.password
        })
        if not login_response or not login_response.session:
            raise Exception("No se pudo iniciar sesión automáticamente después del registro.")

        return AuthTokenResponse(
            access_token=login_response.session.access_token,
            user={
                "id": str(supabase_uid),
                "email": student_in.email,
                "nombre": student_in.nombre,
                "rol": "estudiante"
            }
        )

    except Exception as e:
        db.rollback()
        try:
            client = get_supabase_client()
            if client:
                client.auth.admin.delete_user(str(supabase_uid))
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al registrar perfil en base de datos: {str(e)}"
        )
