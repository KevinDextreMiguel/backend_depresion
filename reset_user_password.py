import sys
from app.security import get_supabase_client
from app.database import SessionLocal
from app.models import Usuario

def reset_password(email, new_password):
    db = SessionLocal()
    supabase = get_supabase_client()
    if not supabase:
        print("Error: Supabase client not configured.")
        return
        
    print(f"Searching for user with email: {email}...")
    
    # 1. Find user in Supabase Auth
    try:
        users = supabase.auth.admin.list_users()
        target_auth_user = None
        for u in users:
            if u.email.lower() == email.lower():
                target_auth_user = u
                break
                
        if not target_auth_user:
            print(f"Error: User with email '{email}' not found in Supabase Auth.")
            return
            
        # 2. Update password in Supabase Auth
        supabase.auth.admin.update_user_by_id(
            target_auth_user.id,
            {"password": new_password}
        )
        print(f"Successfully updated password in Supabase Auth for {email}!")
        
        # 3. Verify they exist in our local database 'usuario' table
        db_user = db.query(Usuario).filter(Usuario.correo == email).first()
        if not db_user:
            print(f"Warning: User exists in Supabase Auth but is missing from the local 'usuario' table.")
            # Create local profile if missing
            print("Creating local profile...")
            db_user = Usuario(
                id_usuario=target_auth_user.id,
                nombre=target_auth_user.user_metadata.get("name", "Admin"),
                correo=email,
                rol=target_auth_user.user_metadata.get("role", "admin"),
                activo=True
            )
            db.add(db_user)
            db.commit()
            print("Successfully created local database profile!")
            
    except Exception as e:
        print(f"Error during password reset: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python reset_user_password.py <email> <new_password>")
        sys.exit(1)
        
    email_arg = sys.argv[1]
    pwd_arg = sys.argv[2]
    reset_password(email_arg, pwd_arg)
