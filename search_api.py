import re
import redis
import psycopg2
import json
import time
import requests
from datetime import datetime
from jamo import h2j, j2hcj

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


# ==========================================
# [기능 고도화 1] 검색어 형태소 정규화 및 동의어 필터링
# ==========================================
def normalize_and_synonym_filter(keyword):
    """
    조사('은', '는', '이', '가', '어때', '언제야' 등)를 제거하고 동의어 사전을 거쳐 대표 키워드로 정규화함.
    예: '성탄절 언제야' -> '크리스마스'
        '부산 날씨 어때' -> '부산 날씨'
    """
    clean_kw = keyword.strip()
    
    # 1. 불필요한 조사, 서술어 어미 제거 정규식
    clean_kw = re.sub(r'(은|는|이|가|을|를|의|에|어때|언제야|현재|정보|날짜|디데이|d-day)$', '', clean_kw)
    clean_kw = clean_kw.strip()
    
    # 2. 동의어 매핑 사전
    synonym_dict = {
        "성탄절": "크리스마스",
        "x-mas": "크리스마스",
        "새해": "신정",
        "신정": "신정",
        "ㄴㅆ": "날씨",
        "블로": "블로그",
        "포스트": "블로그",
        "뉴스기사": "뉴스",
        "기사": "뉴스"
    }
    
    # 공백 단위로 쪼개어 동의어 치환 후 재조립
    words = clean_kw.split()
    mapped_words = [synonym_dict.get(w, w) for w in words]
    normalized_result = " ".join(mapped_words)
    
    # 전체 문장이 동의어에 있는 경우 예외 처리
    if normalized_result in synonym_dict:
        normalized_result = synonym_dict[normalized_result]
        
    return normalized_result


# ==========================================
# [기능 고도화 3] 실시간 인기 검색어 TOP 10 랭킹 집계
# ==========================================
def get_realtime_trending_keywords():
    """
    Redis의 Sorted Set을 활용하여 검색 빈도가 가장 높은 상위 10개 키워드를 실시간 반환함.
    """
    try:
        # 스코어(검색 횟수) 기준 내림차순으로 상위 10개 데이터 조회
        trending_raw = r.zrevrange("saver:popular_scores", 0, 9, withscores=True)
        trending_list = []
        for rank, (kw, score) in enumerate(trending_raw, 1):
            trending_list.append({
                "순위": rank,
                "키워드": kw,
                "검색횟수": int(score)
            })
        return trending_list
    except Exception:
        return []


# ==========================================
# [기능 3] Redis 기반 Rate Limiter
# ==========================================
def is_rate_limited(client_ip, limit=2, period=4):
    key = f"rate:limit:{client_ip}"
    try:
        current_requests = r.get(key)
        if current_requests and int(current_requests) >= limit:
            return True
        
        pipeline = r.pipeline()
        pipeline.incr(key)
        pipeline.expire(key, period)
        pipeline.execute()
    except Exception as e:
        print(f"⚠️ [Rate Limiter 경고] Redis 연산 실패: {e}")
    return False


# ==========================================
# [기능 1] 초성 분리 및 오타 교정 정밀 엔진
# ==========================================
def get_jamo_string(text):
    return j2hcj(h2j(text))

def initialize_autocomplete_database():
    sample_keywords = ["날씨", "오늘 날씨", "날씨 정보", "블로그 포스트", "최신 뉴스", "계산기", "기념일", "크리스마스 날짜"]
    try:
        for kw in sample_keywords:
            jamo_key = get_jamo_string(kw)
            r.hset("saver:autocomplete:jamo_map", jamo_key, kw)
            r.zadd("saver:popular_scores", {kw: 1})
    except Exception as e:
        print(f"⚠️ 자동완성 사전 초기화 실패: {e}")

initialize_autocomplete_database()


def search_autocomplete(keyword):
    input_jamo = get_jamo_string(keyword)
    suggestions = []
    
    try:
        all_jamos = r.hgetall("saver:autocomplete:jamo_map")
        for j_key, real_val in all_jamos.items():
            if input_jamo in j_key:
                suggestions.append(real_val)
                
        populars = r.zrevrange("saver:popular_scores", 0, -1)
        for p_kw in populars:
            if keyword in p_kw and p_kw not in suggestions:
                suggestions.append(p_kw)
    except Exception:
        pass
        
    return suggestions[:5]


# ==========================================
# [기능 2] 다중 지역 동적 날씨 제공 엔진
# ==========================================
def get_realtime_weather(city_name="Seoul"):
    city_map = {
        "서울": "Seoul", "부산": "Busan", "인천": "Incheon", 
        "대구": "Daegu", "대전": "Daejeon", "광주": "Gwangju", 
        "울산": "Ulsan", "제주": "Jeju"
    }
    
    target_city_en = city_map.get(city_name, "Seoul")
    target_city_ko = city_name if city_name in city_map else "서울시"

    if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == "YOUR_API_KEY_HERE":
        return {
            "location": target_city_ko,
            "temperature": "22.1°C" if city_name == "부산" else "24.5°C",
            "status": "비 (Rainy)" if city_name == "부산" else "흐림 (Cloudy)",
            "humidity": "90%" if city_name == "부산" else "85%",
            "wind_speed": "4.1 m/s" if city_name == "부산" else "3.2 m/s",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "source": f"Mock ({target_city_ko} 매칭 완공)"
        }
    
    url = f"https://api.openweathermap.org/data/2.5/weather?q={target_city_en}&appid={OPENWEATHER_API_KEY}&units=metric&lang=kr"
    try:
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            data = response.json()
            return {
                "location": target_city_ko,
                "temperature": f"{data['main']['temp']:.1f}°C",
                "status": data['weather'][0]['description'],
                "humidity": f"{data['main']['humidity']}%",
                "wind_speed": f"{data['wind']['speed']} m/s",
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "source": "OpenWeatherMap API"
            }
        else:
            raise Exception()
    except Exception:
        return {
            "location": target_city_ko,
            "temperature": "24.5°C",
            "status": "기상청 통신 제한",
            "humidity": "85%",
            "wind_speed": "3.2 m/s",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "source": "Fallback"
        }

def parse_weather_city(keyword):
    cities = ["서울", "부산", "인천", "대구", "대전", "광주", "울산", "제주"]
    for city in cities:
        if city in keyword:
            return city
    return "서울"


# ==========================================
# 기념일/디데이 연산 엔진
# ==========================================
def calculate_anniversary_dday(keyword):
    anniversaries = {
        "크리스마스": "2026-12-25",
        "성탄절": "2026-12-25",
        "광복절": "2026-08-15",
        "추석": "2026-09-25",
        "한글날": "2026-10-09",
        "신정": "2027-01-01",
        "새해": "2027-01-01"
    }
    
    weekday_map = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    
    target_event = None
    for key in anniversaries.keys():
        if key in keyword:
            target_event = key
            break
            
    if not target_event:
        return None
        
    target_date_str = anniversaries[target_event]
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    current_date = datetime(2026, 7, 15) # 기준일 고정
    
    delta = target_date - current_date
    days_left = delta.days
    
    weekday_name = weekday_map[target_date.weekday()]
    
    if days_left > 0:
        d_day_str = f"D-{days_left}"
        msg = f"{target_event}까지 {days_left}일 남았습니다!"
    elif days_left == 0:
        d_day_str = "D-Day"
        msg = f"오늘이 바로 즐거운 {target_event}입니다! 🎉"
    else:
        d_day_str = f"D+{abs(days_left)}"
        msg = f"올해 {target_event}은(는) 이미 지났습니다."

    emoji = "🎄" if "크리스마스" in target_event or "성탄절" in target_event else "🇰🇷" if "광복" in target_event else "🌕" if "추석" in target_event else "🗓️"

    return {
        "event_name": target_event,
        "date": f"{target_date_str} ({weekday_name})",
        "d_day": d_day_str,
        "message": f"{msg} {emoji}"
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
            "keywords": ["날씨", "기온", "비", "우산", "날씨 어때", "온도", "ㄴㅆ"],
            "msg": "날씨 탭에서 실시간 전국 기상 정보를 확인해보세요!"
        },
        "news": {
            "keywords": ["뉴스", "기사", "소식", "신문", "보도"],
            "msg": "뉴스 탭에서 HUFS 및 청년 창업 최신 뉴스를 확인해보세요!"
        },
        "anniversary": {
            "keywords": ["크리스마스", "성탄절", "광복절", "추석", "한글날", "신정", "새해", "기념일", "디데이"],
            "msg": "기념일 탭에서 다가오는 공휴일과 디데이 일정을 확인해보세요!"
        }
    }
    
    jamo_keyword = get_jamo_string(keyword)
    for target_id, info in intent_map.items():
        if target_id == "weather" and "ㄴㅆ" in jamo_keyword:
            return {
                "target_id": "weather",
                "recommend_message": info["msg"]
            }
        for kw in info["keywords"]:
            if kw in keyword:
                return {
                    "target_id": target_id,
                    "recommend_message": info["msg"]
                }
    return None


# ==========================================
# Core Search Logic (위치별 캐싱 및 인기 검색어 랭킹 탑재)
# ==========================================
def get_saver_search_result(raw_keyword, client_ip="127.0.0.1"):
    # [Rate Limit 적용]
    if is_rate_limited(client_ip, limit=2, period=4):
        return {
            "error": "Too Many Requests",
            "message": "너무 빠른 검색 요청이 감지되었습니다. 잠시 후 다시 시도해 주세요 (4초 내 최대 2회 제한)."
        }

    # [고도화 1] 검색어 형태소 정규화 및 동의어 처리
    keyword = normalize_and_synonym_filter(raw_keyword)
    print(f"\n[검색엔진 가동] 원본: '{raw_keyword}' ➡️ 정규화: '{keyword}' (IP: {client_ip})")
    
    start_time = time.time()
    
    # [고도화 2] Redis 검색 캐싱 검증 (Cache Hit 레이어)
    cache_key = f"saver:cache:{keyword}"
    try:
        cached_data = r.get(cache_key)
        if cached_data:
            latency = (time.time() - start_time) * 1000
            result_json = json.loads(cached_data)
            # 캐시 응답 속도를 명시하고 반환
            result_json["SAVER_Special_Search"]["검색속도"] = f"{latency:.2f}ms (Cache Hit)"
            # 캐시가 조회되었어도 실시간 랭킹 가중치는 갱신
            r.zincrby("saver:popular_scores", 1, keyword)
            return result_json
    except Exception as e:
        print(f"⚠️ [캐싱 에러] Redis 읽기 실패: {e}")

    # --- Cache Miss 구간 (원래 검색 로직 수행) ---
    
    # 1. 계산기 기능 우선 처리
    math_result = evaluate_math_expression(keyword)
    if math_result:
        latency = (time.time() - start_time) * 1000
        output = {
            "SAVER_Special_Search": {
                "검색속도": f"{latency:.2f}ms",
                "타입": "calculator",
                "결과": math_result,
                "추천_결과": None,
                "연관_검색어_추천": []
            }
        }
        return output

    # 2. 유저 검색 의도 분석 처리
    user_intent = detect_user_intent(keyword)

    # 3. 특정 서비스 추천(의도)이 확실히 감지되었다면 처리
    if user_intent:
        realtime_widget_data = None
        if user_intent["target_id"] == "weather":
            target_city = parse_weather_city(keyword)
            realtime_widget_data = get_realtime_weather(target_city)
        elif user_intent["target_id"] == "anniversary":
            realtime_widget_data = calculate_anniversary_dday(keyword)

        latency = (time.time() - start_time) * 1000
        related_keywords = search_autocomplete(keyword)
        
        output = {
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
                    "realtime_data": realtime_widget_data
                },
                "연관_검색어_추천": related_keywords
            }
        }
        
        # [고도화 2] 연산 결과 Redis 캐싱 등록 (TTL: 60초)
        try:
            r.setex(cache_key, 60, json.dumps(output))
        except Exception:
            pass
            
        return output

    # 4. 일반 키워드 자동완성 탐색
    related_keywords = search_autocomplete(keyword)
    if not related_keywords:
        related_keywords = [f"{keyword} 추천", f"{keyword} 최신 뉴스", f"HUFS {keyword}"]

    # 5. 일반 키워드인 경우 PostgreSQL GIN DB 통합 검색 수행
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
            
        # 스코어 누적 및 자소 사전 등록
        r.zincrby("saver:popular_scores", 1, keyword)
        r.hset("saver:autocomplete:jamo_map", get_jamo_string(keyword), keyword)
            
    except Exception as e:
        print(f"❌ [디비 에러] {e}")

    latency = (time.time() - start_time) * 1000

    output = {
        "SAVER_Special_Search": {
            "검색속도": f"{latency:.2f}ms",
            "타입": "search",
            "최선의_결과": best_result,
            "추천_결과": user_intent,
            "연관_검색어_추천": related_keywords
        }
    }
    
    # [고도화 2] 결과 캐싱 등록 (60초 만료)
    try:
        r.setex(cache_key, 60, json.dumps(output))
    except Exception:
        pass

    return output

if __name__ == "__main__":
    print("=" * 60)
    print("⚡ SAVER Hyper-Optimized Search Engine v5.0 (Ultimate Edition)")
    print("=" * 60)
    
    try:
        while True:
            print("\n" + "-"*40)
            print("1. 검색하기 | 2. 실시간 인기 검색어 랭킹 보기 | 3. 종료 (exit)")
            menu = input("👉 메뉴 번호 또는 키워드를 입력하세요: ").strip()
            
            if not menu:
                continue
            if menu.lower() == 'exit' or menu == '3':
                print("👋 검색 백엔드 코어 시스템을 안전하게 종료합니다.")
                break
                
            if menu == '2':
                # [고도화 3] 실시간 인기 랭킹 확인 메뉴
                ranking = get_realtime_trending_keywords()
                print("\n🔥 [SAVER 실시간 인기 검색어 TOP 10] 🔥")
                for rank_item in ranking:
                    print(f"[{rank_item['순위']}위] {rank_item['키워드']} (검색 수: {rank_item['검색횟수']}회)")
                print("-"*40)
                continue
            
            # 1번 메뉴 혹은 키워드 직접 입력 처리
            search_keyword = menu if menu != '1' else input("🔍 검색 키워드 입력: ").strip()
            if not search_keyword:
                continue
                
            final_output = get_saver_search_result(search_keyword, client_ip="127.0.0.1")
            
            if final_output:
                print("\n" + "="*20 + " [프론트엔드 전달 API 응답 예시] " + "="*20)
                print(json.dumps(final_output, indent=4, ensure_ascii=False))
                print("="*64)
    finally:
        if pg_cursor: pg_cursor.close()
        if pg_conn: pg_conn.close()