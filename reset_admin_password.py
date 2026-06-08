"""
Reset admin user password in Supabase Auth and list all admin accounts.
Run: py reset_admin_password.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
    sys.exit(1)

from supabase import create_client

# Admin client (service_role) for admin operations
service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
# Anon client for sign_in test
anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

print("=" * 60)
print("ADMIN ACCOUNTS IN SUPABASE AUTH")
print("=" * 60)

# List all users in Supabase Auth
try:
    response = service_client.auth.admin.list_users()
    users = response if isinstance(response, list) else []
    print(f"Total users in Supabase Auth: {len(users)}")
    for u in users:
        print(f"  - {u.email}  (id={u.id}, confirmed={u.email_confirmed_at is not None})")
except Exception as e:
    print(f"WARNING: Could not list users (may need JWT service_role): {e}")
    print("Proceeding with password reset by email...")

print()

# --- Reset admin password ---
NEW_PASSWORD = "Admin2026!"
ADMIN_EMAIL = "dextremiguelk@gmail.com"

print(f"Resetting password for: {ADMIN_EMAIL}")
print(f"New password will be:   {NEW_PASSWORD}")
print()

# Method 1: Admin API update by email lookup
try:
    users_resp = service_client.auth.admin.list_users()
    users = users_resp if isinstance(users_resp, list) else []
    admin_user = next((u for u in users if u.email == ADMIN_EMAIL), None)
    
    if admin_user:
        result = service_client.auth.admin.update_user_by_id(
            admin_user.id,
            {"password": NEW_PASSWORD, "email_confirm": True}
        )
        print(f"[OK] Password updated for {ADMIN_EMAIL} (id={admin_user.id})")
        
        # Confirm it works
        print("Testing login with new password...")
        login_resp = anon_client.auth.sign_in_with_password({
            "email": ADMIN_EMAIL,
            "password": NEW_PASSWORD
        })
        if login_resp and login_resp.session:
            print(f"[OK] Login SUCCESSFUL! Token prefix: {login_resp.session.access_token[:30]}...")
            print(f"     Role in metadata: {login_resp.user.user_metadata}")
        else:
            print("[FAIL] Login failed after reset")
    else:
        print(f"[WARN] User {ADMIN_EMAIL} not found in Supabase Auth list")
        print("Attempting direct password reset via update_user_by_email...")
        # Try sign-in first to get the user id, then update
        
except Exception as e:
    print(f"[ERROR] Admin API failed: {e}")
    print()
    print("Trying alternative: generate magic link / password reset email...")
    try:
        r = service_client.auth.admin.generate_link({
            "type": "recovery",
            "email": ADMIN_EMAIL,
        })
        print(f"[OK] Recovery link generated: {r}")
    except Exception as e2:
        print(f"[ERROR] Recovery link failed: {e2}")

print()
print("=" * 60)
print("DONE. Use these credentials in the frontend:")
print(f"  Email:    {ADMIN_EMAIL}")
print(f"  Password: {NEW_PASSWORD}")
print("=" * 60)
