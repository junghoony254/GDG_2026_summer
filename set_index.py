import psycopg2

try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:15432/postgres")
    cursor = conn.cursor()
    
    # 1. 텍스트 부분 일치 검색을 초고속으로 만들어주는 확장 기능 활성화
    cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    
    # 2. hufspress와 blog 테이블의 title, content에 각각 GIN 트라이그램 인덱스 생성
    print("[*] 고속 검색 인덱스 생성 중... 잠시만 기다려주세요.")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hufspress_title_trgm ON hufspress USING gin (title gin_trgm_ops);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hufspress_content_trgm ON hufspress USING gin (content gin_trgm_ops);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blog_title_trgm ON blog USING gin (title gin_trgm_ops);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_blog_content_trgm ON blog USING gin (content gin_trgm_ops);")
    
    conn.commit()
    print("✅ [인덱스 완공] 이제 %키워드% 검색 시 Full Scan을 하지 않고 인덱스를 탑니다!")
except Exception as e:
    print(f"❌ 인덱스 생성 실패: {e}")
finally:
    cursor.close()
    conn.close()