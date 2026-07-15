import redis
import psycopg2
import json
import time

# ====================================================
# 🔌 [하이퍼 최적화] 글로벌 커넥션 초기화
# ====================================================
r = None
pg_conn = None
pg_cursor = None

try:
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    r.ping()
    
    pg_conn = psycopg2.connect("postgresql://postgres:postgres@localhost:15432/postgres")
    pg_cursor = pg_conn.cursor()
    print("✅ [인프라 상시 커넥션 수립 완료]")
except Exception as e:
    print(f"❌ 인프라 연결 실패: {e}")
    exit()

def get_saver_search_result(keyword):
    # 💡 최적화: 유저 입력에 불필요한 공백 제거
    keyword = keyword.strip()
    print(f"\n[검색엔진 가동] 유저 입력 키워드: '{keyword}'")
    
    start_time = time.time()
    
    # ----------------------------------------------------
    # 로직 A: [연관 검색어] 구현 (Valkey/Redis 인메모리 고속 연산)
    # ----------------------------------------------------
    try:
        r.zincrby("saver:popular_scores", 1, keyword)
        all_keywords = r.zrevrange("saver:popular_scores", 0, -1)
        related_keywords = [kw for kw in all_keywords if keyword in kw and kw != keyword][:5]
    except Exception:
        related_keywords = []
        
    if not related_keywords:
        related_keywords = [f"{keyword} 추천", f"{keyword} 최신 뉴스", f"HUFS {keyword}"]

    # ----------------------------------------------------
    # 로직 B: [최선의 결과] 구현 (GIN Trigram Index 기반 초고속 검색)
    # ----------------------------------------------------
    best_result = None
    try:
        # 방금 생성한 트라이그램 인덱스를 활용하여 디스크 블록 전체를 스캔하지 않고 타겟 데이터만 즉시 타격
        query = """
            SELECT 'hufspress' as source, title, content FROM hufspress WHERE title LIKE %s OR content LIKE %s
            UNION ALL
            SELECT 'blog' as source, title, content FROM blog WHERE title LIKE %s OR content LIKE %s
            LIMIT 1;
        """
        search_pattern = f"%{keyword}%"
        pg_cursor.execute(query, (search_pattern, search_pattern, search_pattern, search_pattern))
        row = pg_cursor.fetchone()
        
        if row:
            raw_content = row[2] if row[2] else ""
            summary_text = raw_content[:100] + "..." if len(raw_content) > 100 else raw_content
            
            best_result = {
                "게시처": row[0],
                "제목": row[1],
                "요약본(100자)": summary_text
            }
        else:
            best_result = {
                "게시처": "None",
                "제목": f"'{keyword}'에 대한 검색 결과가 없습니다.",
                "요약본(100자)": "데이터베이스 내에 매칭되는 본문이 없습니다."
            }
            
    except Exception as e:
        print(f"❌ [디비 에러] {e}")

    # 모든 내부 IO 오버헤드가 사라진 순수 메모리/인덱스 연산 속도
    latency = (time.time() - start_time) * 1000

    return {
        "SAVER_Special_Search": {
            "검색속도": f"{latency:.2f}ms",
            "최선의_결과": best_result,
            "연관_검색어_추천": related_keywords
        }
    }

if __name__ == "__main__":
    print("=" * 60)
    print("⚡ SAVER Hyper-Optimized Search Engine v3.0 (Trigram Indexing)")
    print("=" * 60)
    
    try:
        while True:
            user_input = input("\n🟢 검색 키워드를 입력하세요 (종료: exit): ").strip()
            if not user_input:
                continue
            if user_input.lower() == 'exit':
                print("👋 백엔드 검색 엔진 시뮬레이터를 종료합니다.")
                break
                
            final_output = get_saver_search_result(user_input)
            
            if final_output:
                print("\n" + "="*20 + " [프론트엔드 전달 API 응답 예시] " + "="*20)
                print(json.dumps(final_output, indent=4, ensure_ascii=False))
                print("="*64)
    finally:
        if pg_cursor: pg_cursor.close()
        if pg_conn: pg_conn.close()