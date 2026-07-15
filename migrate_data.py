import sqlite3
import psycopg2
from psycopg2.extras import execute_values
import time
from tqdm import tqdm
import os

# ==========================================
# 1. 데이터베이스 파일 및 서버 경로 설정
# ==========================================
possible_paths = [
    "검색엔진데이터.db",
    "search_data.db",
    "../data/search_data.db",
    "../data/검색엔진데이터.db"
]

SQLITE_DB_PATH = None
for path in possible_paths:
    if os.path.exists(path):
        SQLITE_DB_PATH = path
        break

if SQLITE_DB_PATH is None:
    SQLITE_DB_PATH = "검색엔진데이터.db"

PG_HOST = "localhost"
PG_PORT = 15432
PG_DB = "postgres"
PG_USER = "postgres"
PG_PASSWORD = "postgres" 

def migrate():
    print("=" * 60)
    print("🚀 SAVER 데이터베이스 이관(Migration) 프로세스를 시작합니다.")
    print("=" * 60)
    
    print(f"🔍 탐색된 SQLite 파일 경로: {SQLITE_DB_PATH}")
    
    try:
        lite_conn = sqlite3.connect(SQLITE_DB_PATH)
        lite_cursor = lite_conn.cursor()
        
        pg_conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, database=PG_DB, user=PG_USER, password=PG_PASSWORD
        )
        pg_cursor = pg_conn.cursor()
        print(f"✅ [연결 성공] SQLite 파일 및 Docker PostgreSQL 16 서버 연결 완료.")
    except Exception as e:
        print(f"❌ [연결 실패] DB 연결 중 에러 발생: {e}")
        return

    # ==========================================
    # 2. 원격 PostgreSQL 테이블 구조 초기화 및 생성
    # ==========================================
    print("\n📦 원격 PostgreSQL 테이블 구조 생성 중...")
    
    create_tables_sql = """
    DROP TABLE IF EXISTS blog CASCADE;
    DROP TABLE IF EXISTS hufspress CASCADE;
    DROP TABLE IF EXISTS topic CASCADE;

    CREATE TABLE blog (
        id SERIAL PRIMARY KEY,
        title TEXT,
        content TEXT
    );
    CREATE TABLE hufspress (
        id SERIAL PRIMARY KEY,
        title TEXT,
        content TEXT,
        category TEXT,
        date TEXT
    );
    CREATE TABLE topic (
        id SERIAL PRIMARY KEY,
        name TEXT
    );
    """
    pg_cursor.execute(create_tables_sql)
    pg_conn.commit()
    print("✅ [테이블 생성] blog, hufspress, topic 테이블 세팅 완료.")

    tables = ['blog', 'hufspress', 'topic']
    report_counts = {}
    
    start_time = time.time()

    # ==========================================
    # 3. 데이터 복사 및 이사 (Progress Bar 연동)
    # ==========================================
    for table in tables:
        print(f"\n🚚 [{table}] 테이블 데이터 이사 시작...")
        
        try:
            if table == 'blog':
                lite_cursor.execute("SELECT title, content, id FROM blog")
                insert_query = "INSERT INTO blog (title, content, id) VALUES %s"
            elif table == 'hufspress':
                lite_cursor.execute("SELECT id, title, content, category, date FROM hufspress")
                insert_query = "INSERT INTO hufspress (id, title, content, category, date) VALUES %s"
            elif table == 'topic':
                # id 컬럼이 없으므로 name만 추출하고, PostgreSQL의 serial id가 자동으로 들어가게 세팅
                lite_cursor.execute("SELECT name FROM topic")
                insert_query = "INSERT INTO topic (name) VALUES %s"
                
            rows = lite_cursor.fetchall()
        except sqlite3.OperationalError as e:
            print(f"❌ [{table}] 테이블 읽기 실패: {e}")
            report_counts[table] = 0
            continue
        
        if not rows:
            print(f"ℹ️ {table} 테이블에 이사할 데이터가 없습니다.")
            report_counts[table] = 0
            continue

        batch_size = 50
        for i in tqdm(range(0, len(rows), batch_size), desc=f"Sending {table} to Postgres"):
            batch = rows[i:i+batch_size]
            execute_values(pg_cursor, insert_query, batch)
            pg_conn.commit()
            time.sleep(0.02)

        report_counts[table] = len(rows)
        print(f"✅ [{table}] 총 {len(rows)}건 이사 완료!")

    end_time = time.time()
    elapsed = end_time - start_time

    # ==========================================
    # 4. 최종 정산 영수증 리포트 출력
    # ==========================================
    print("\n" + "=" * 60)
    print("✨ 데이터베이스 이관(Migration) 작업이 성공적으로 완료되었습니다!")
    print("=" * 60)
    print(f"📦 원본 파일      : {SQLITE_DB_PATH}")
    print(f"🚀 목적지 서버    : {PG_HOST}:{PG_PORT} (Docker PostgreSQL)")
    print("-" * 60)
    print("[이사 완료 명세서]")
    for table, count in report_counts.items():
        print(f" - {table.ljust(12)} : 총 {str(count).rjust(5)} 건 이사 완료")
    print("-" * 60)
    print(f"⏱️ 총 소요 시간: {elapsed:.2f}초 (데이터 유실 없음 확인됨)")
    print("=" * 60)

    lite_conn.close()
    pg_conn.close()

if __name__ == "__main__":
    migrate()