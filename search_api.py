import re
import redis
import psycopg2
import json
import time
import requests

r = None
pg_conn = None
pg_cursor = None

# OpenWeatherMap API 키 (가입 후 발급받은 키가 있다면 여기에 입력, 없어도 모크 데이터로 작동함)
OPENWEATHER_API_KEY = "YOUR_API_KEY_HERE"

try:
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    r.ping()
    
    pg_conn = psycopg2.connect("postgresql://postgres:postgres@localhost:15432/postgres")
    pg_cursor = pg_conn.cursor()
    print("✅ [인프라 상시 커넥션 수립 완료]")
except Exception as e:
    print(f"❌ 인프라 연결 실패: {e}")
    exit()

def get_realtime_weather():
    """
    OpenWeatherMap API를 사용해 서울의 실시간 날씨를 가져옴.
    API 키가 없거나 에러 발생 시 최신 업데이트된 날짜 기준의 고품질 모크 데이터를 반환함.
    """
    if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == "YOUR_API_KEY_HERE":
        # API 키가 설정되지 않았을 때 반환할 스마트 모크 데이터
        return {
            "location": "서울시",
            "temperature": "24.5°C",
            "status": "흐림 (Rainy)",
            "humidity": "85%",
            "wind_speed": "3.2 m/s",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "source": "Mock (API Key Missing)"
        }
    
    # 실제 API 호출 구간 (도시: 서울)
    url = f"https://api.openweathermap.org/data/2.5/weather?q=Seoul&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    try:
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            return {
                "location": "서울시",
                "temperature": f"{data['main']['temp']:.1f}°C",
                "status": data['weather'][0]['description'],
                "humidity": f"{data['main']['humidity']}%",
                "wind_speed": f"{data['wind']['speed']} m/s",
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "source": "OpenWeatherMap API"
            }
        else:
            raise Exception("API Response Error")
    except Exception:
        # API 호출 중 타임아웃이나 에러 발생 시 Fallback 데이터로 방어
        return {
            "location": "서울시",
            "temperature": "24.5°C",
            "status": "흐림 (Rainy)",
            "humidity": "85%",
            "wind_speed": "3.2 m/s",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "source": "Fallback (Connection Timeout)"
        }

def evaluate_math_expression(keyword):
    clean_keyword = keyword.replace(" ", "")
    clean_keyword = re.sub(r'의?세제곱', '**3', clean_keyword)
    clean_keyword = re.sub(r'의?제곱', '**2', clean_keyword)
    
    math_pattern = r'^[\d+\-*/().\s]+$'
    
    if re.match(math_pattern, clean_keyword):
        try:
            result = eval(clean_keyword)
            if isinstance(result, (int, float)):
                return {
                    "type": "calculator",
                    "expression": keyword,
                    "result": str(result)
                }
        except Exception:
            return None
    return None

def detect_user_intent(keyword):
    intent_map = {
        "blog": {
            "keywords": ["블로그", "글", "게시물", "포스트"],
            "msg": "블로그 탭에서 다양한 후기와 포스트를 확인해보세요!"
        },
        "weather": {
            "keywords": ["날씨", "기온", "비", "우산", "날씨 어때", "온도"],
            "msg": "날씨 탭에서 실시간 전국 기상 정보를 확인해보세요!"
        },
        "news": {
            "keywords": ["뉴스", "기사", "소식", "신문", "보도"],
            "msg": "뉴스 탭에서 HUFS 및 청년 창업 최신 뉴스를 확인해보세요!"
        }
    }
    
    for target_id, info in intent_map.items():
        for kw in info["keywords"]:
            if kw in keyword:
                return {
                    "target_id": target_id,
                    "recommend_message": info["msg"]
                }
    return None

def get_saver_search_result(keyword):
    keyword = keyword.strip()
    print(f"\n[검색엔진 가동] 유저 입력 키워드: '{keyword}'")
    
    start_time = time.time()
    
    # 1. 계산기 기능 우선 처리
    math_result = evaluate_math_expression(keyword)
    if math_result:
        latency = (time.time() - start_time) * 1000
        return {
            "SAVER_Special_Search": {
                "검색속도": f"{latency:.2f}ms",
                "타입": "calculator",
                "결과": math_result,
                "추천_결과": None,
                "연관_검색어_추천": []
            }
        }

    # 2. 유저 검색 의도 분석(서비스 추천) 처리
    user_intent = detect_user_intent(keyword)

    # 3. 특정 서비스 추천(의도)이 확실히 감지되었다면 처리
    if user_intent:
        # [고도화 추가] 만약 감지된 의도가 'weather' 라면 실시간 날씨 데이터 직접 탑재
        realtime_weather_data = None
        if user_intent["target_id"] == "weather":
            realtime_weather_data = get_realtime_weather()

        latency = (time.time() - start_time) * 1000
        related_keywords = [f"{keyword} 추천", f"{keyword} 현재 상황", f"실시간 {keyword}"]
        return {
            "SAVER_Special_Search": {
                "검색속도": f"{latency:.2f}ms",
                "타입": "recommend",
                "최선의_결과": {
                    "게시처": "Widget",
                    "제목": f"{user_intent['target_id'].upper()} 실시간 정보 매칭",
                    "요약본(100자)": user_intent['recommend_message']
                },
                "추천_결과": {
                    "target_id": user_intent["target_id"],
                    "recommend_message": user_intent["recommend_message"],
                    "realtime_data": realtime_weather_data  # 실시간 데이터 실어 보내기
                },
                "연관_검색어_추천": related_keywords
            }
        }

    # 4. 연관 검색어 구현 (Valkey/Redis 인메모리 연산)
    try:
        r.zincrby("saver:popular_scores", 1, keyword)
        all_keywords = r.zrevrange("saver:popular_scores", 0, -1)
        related_keywords = [kw for kw in all_keywords if keyword in kw and kw != keyword][:5]
    except Exception:
        related_keywords = []
        
    if not related_keywords:
        related_keywords = [f"{keyword} 추천", f"{keyword} 최신 뉴스", f"HUFS {keyword}"]

    # 5. 일반 키워드인 경우에만 기존처럼 PostgreSQL GIN DB 통합 검색 수행
    best_result = None
    try:
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

    latency = (time.time() - start_time) * 1000

    return {
        "SAVER_Special_Search": {
            "검색속도": f"{latency:.2f}ms",
            "타입": "search",
            "최선의_결과": best_result,
            "추천_결과": user_intent,
            "연관_검색어_추천": related_keywords
        }
    }

if __name__ == "__main__":
    print("=" * 60)
    print("⚡ SAVER Hyper-Optimized Search Engine v3.3 (Weather API Enabled)")
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