from supabase import create_client

SUPABASE_URL = "https://iheptwqcrfeeluxmpizx.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImloZXB0d3FjcmZlZWx1eG1waXp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzMTU4MDUsImV4cCI6MjA4Nzg5MTgwNX0.M9dWuluNRSqTdaNbyqLD969BIR9DQTnHLH6jJf7OaBQ"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

tables = ["users", "departments", "tasks", "reports"]

for table in tables:
    try:
        res = supabase.table(table).select("*").limit(1).execute()
        print(f"✅ {table} — connected")
    except Exception as e:
        print(f"❌ {table} — failed: {e}")