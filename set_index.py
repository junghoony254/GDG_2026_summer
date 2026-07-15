import psycopg2

try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:15432/postgres")
    cursor = conn.cursor()
    
    cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    
    print("[*] 고속 검색 인덱스 생성 중... 잠시만 기다려주세요.")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hufspress_title_trgm ON hufspress USING gin (title gin_trgm_ops);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hufspress_content_trgm ON hufspress USING gin (content gin_trgm_ops);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blog_title_trgm ON blog USING gin (title gin_trgm_ops);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blog_content_trgm ON blog USING gin (content gin_trgm_ops);")
    
    conn.commit()
    print("✅ [인덱스 완공]")
except Exception as e:
    print(f"❌ 인덱스 생성 실패: {e}")
finally:
    cursor.close()
    conn.close()