"""
Diagnose admin login issue: check DB state + test login via both clients.
Run: py diagnose_admin_login.py
"""
import os, sys
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from sqlalchemy import create_engine, text
from app.config import settings

SUPABASE_URL = settings.SUPABASE_URL
SERVICE_KEY = settings.SUPABASE_SERVICE_KEY
ANON_KEY = settings.SUPABASE_ANON_KEY

print("=" * 60)
print("1) SUPABASE AUTH STATE")
print("=" * 60)

service_client = create_client(SUPABASE_URL, SERVICE_KEY)
anon_client = create_client(SUPABASE_URL, ANON_KEY)

# Check all admin-role users in Supabase Auth
try:
    users = service_client.auth.admin.list_users()
    print(f"Total users in Supabase Auth: {len(users)}")
except Exception as e:
    print(f"Could not list users: {e}")
    users = []

print()
print("=" * 60)
print("2) DATABASE usuario TABLE (admin role)")
print("=" * 60)

engine = create_engine(settings.DATABASE_URL)
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT id_usuario, correo, rol, activo FROM usuario WHERE rol = 'admin'"
    )).fetchall()
    print(f"Admins in DB: {len(rows)}")
    for r in rows:
        print(f"  id={r[0]}  correo={r[1]}  rol={r[2]}  activo={r[3]}")

print()
print("=" * 60)
print("3) TEST LOGIN — Admin2026!")
print("=" * 60)

ADMIN_EMAIL = "dextremiguelk@gmail.com"
ADMIN_PASSWORD = "Admin2026!"

print(f"  Testing with anon_client.auth.sign_in_with_password...")
try:
    r = anon_client.auth.sign_in_with_password({"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r and r.session:
        print(f"  [OK] anon_client login SUCCESS. uid={r.user.id}")
    else:
        print(f"  [FAIL] anon_client: no session returned")
except Exception as e:
    print(f"  [FAIL] anon_client: {e}")

print(f"  Testing with service_client.auth.sign_in_with_password...")
try:
    r2 = service_client.auth.sign_in_with_password({"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r2 and r2.session:
        print(f"  [OK] service_client login SUCCESS. uid={r2.user.id}")
    else:
        print(f"  [FAIL] service_client: no session returned")
except Exception as e:
    print(f"  [FAIL] service_client: {e}")

print()
print("=" * 60)
print("4) RENDER ENV CHECK — what keys does Render need?")
print("=" * 60)
print(f"  SUPABASE_URL       = {SUPABASE_URL[:30]}...")
print(f"  SUPABASE_ANON_KEY  = {ANON_KEY[:20]}..." if ANON_KEY else "  SUPABASE_ANON_KEY  = [NOT SET]")
print(f"  SUPABASE_SERVICE_KEY = {SERVICE_KEY[:20]}..." if SERVICE_KEY else "  SUPABASE_SERVICE_KEY = [NOT SET]")
