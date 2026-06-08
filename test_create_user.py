"""
Test that admin.create_user() works with the service key.
Run: py test_create_user.py
"""
import os, sys
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

import random, string
test_email = f"test_create_{''.join(random.choices(string.ascii_lowercase, k=8))}@example.com"
test_password = "TestPass999!"

print(f"Testing admin.create_user() with email: {test_email}")
try:
    resp = service_client.auth.admin.create_user({
        "email": test_email,
        "password": test_password,
        "email_confirm": True,
        "user_metadata": {"name": "Test User", "role": "estudiante"}
    })
    print(f"[OK] admin.create_user WORKS: id={resp.user.id}")
    
    # Test login immediately
    login = anon_client.auth.sign_in_with_password({"email": test_email, "password": test_password})
    if login and login.session:
        print(f"[OK] Login after create_user WORKS. Token: {login.session.access_token[:30]}...")
    else:
        print("[FAIL] Login after create_user FAILED")
    
    # Cleanup
    service_client.auth.admin.delete_user(resp.user.id)
    print(f"[OK] Cleanup: user deleted")
    
except Exception as e:
    print(f"[FAIL] admin.create_user failed: {e}")
    print("This means the service key cannot create users via admin API.")
    print()
    print("Testing sign_up() with anon key instead...")
    try:
        resp2 = anon_client.auth.sign_up({
            "email": test_email,
            "password": test_password,
            "options": {"data": {"name": "Test User", "role": "estudiante"}}
        })
        print(f"[OK] sign_up WORKS: id={resp2.user.id}")
        # Try to confirm
        try:
            service_client.auth.admin.update_user_by_id(resp2.user.id, {"email_confirm": True})
            print("[OK] Email confirmed via update_user_by_id")
        except Exception as ce:
            print(f"[WARN] Could not auto-confirm: {ce}")
        service_client.auth.admin.delete_user(resp2.user.id)
    except Exception as e2:
        print(f"[FAIL] sign_up also failed: {e2}")
