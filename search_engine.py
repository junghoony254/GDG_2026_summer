import sqlite3
import os

db_name = "검색엔진데이터.db"

def search_all(keyword):
    if not os.path.exists(db_name):
        print(f"오류: {db_name} 파일이 없습니다.")
        return

    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    print(f"\n🔍 '{keyword}'에 대한 통합 검색 결과:")
    print("=" * 60)

    # 1. 블로그 테이블 검색
    cursor.execute("SELECT title, content, id FROM blog WHERE title LIKE ? OR content LIKE ?;", (f'%{keyword}%', f'%{keyword}%'))
    blog_results = cursor.fetchall()
    for row in blog_results:
        print(f"📌 [출처: 블로그] 제목: {row[0]} (ID: {row[2]})")
        print(f"📝 본문 요약: {row[1][:100]}...\n" + "-"*40)

    # 2. 외대학보 테이블 검색
    cursor.execute("SELECT title, content, category, author, date FROM hufspress WHERE title LIKE ? OR content LIKE ?;", (f'%{keyword}%', f'%{keyword}%'))
    hufs_results = cursor.fetchall()
    for row in hufs_results:
        print(f"📰 [출처: 외대학보 기사] [{row[2]}] 제목: {row[0]} | 기자: {row[3]} ({row[4]})")
        print(f"📝 본문 요약: {row[1][:100]}...\n" + "-"*40)

    # 3. 토픽(태그) 테이블 검색
    cursor.execute("SELECT name, blog_id FROM topic WHERE name LIKE ?;", (f'%{keyword}%',))
    topic_results = cursor.fetchall()
    for row in topic_results:
        print(f"🏷️ [출처: 토픽 태그] 관련 태그 발견: #{row[0]} (연결된 블로그 ID: {row[1]})")
        print("-" * 40)

    total_count = len(blog_results) + len(hufs_results) + len(topic_results)
    print(f"✨ 검색 완료! 총 {total_count}개의 매칭 결과를 찾았습니다.")
    print("=" * 60)
    conn.close()

if __name__ == "__main__":
    user_query = input("검색어를 입력하세요: ")
    search_all(user_query)
