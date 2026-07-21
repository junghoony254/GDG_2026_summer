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

# ==========================================
# [인프라 API 키 설정]
# ==========================================
OPENWEATHER_API_KEY = "YOUR_API_KEY_HERE"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"

try:
    r = redis.Redis(
        host='localhost', 
        port=6379, 
        db=0, 
        decode_responses=True
    )
    r.ping()
    
    pg_conn = psycopg2.connect(
        "postgresql://postgres:postgres@localhost:15432/postgres"
    )
    pg_cursor = pg_conn.cursor()
    print("✅ [인프라 상시 커넥션 수립 완료]")
except Exception as e:
    print(f"❌ 인프라 연결 실패: {e}")
    exit()


# ==========================================
# [알고리즘] 레벤슈타인 편집 거리 알고리즘 (DP 기반)
# ==========================================
def get_levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return get_levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


# ==========================================
# [기능 고도화 1] 검색어 형태소 정규화 및 오타 교정
# ==========================================
def normalize_and_synonym_filter(keyword):
    clean_kw = keyword.strip()
    clean_kw = re.sub(r'(은|는|이|가|을|를|의|에|어때|언제야|현재|정보|날짜|디데이|d-day)$', '', clean_kw)
    clean_kw = clean_kw.strip()
    
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
    
    words = clean_kw.split()
    mapped_words = [synonym_dict.get(w, w) for w in words]
    normalized_result = " ".join(mapped_words)
    
    if normalized_result in synonym_dict:
        normalized_result = synonym_dict[normalized_result]
        
    return normalized_result


def correct_typo_fuzzy(keyword):
    input_jamo = get_jamo_string(keyword)
    best_match = None
    min_distance = 9999
    max_score = -1 
    
    try:
        all_jamos = r.hgetall("saver:autocomplete:jamo_map")
        for j_key, real_val in all_jamos.items():
            dist = get_levenshtein_distance(input_jamo, j_key)
            if dist <= 3:
                score = r.zscore("saver:popular_scores", real_val)
                score = int(score) if score else 0
                
                if real_val in ["날씨", "크리스마스", "학사", "캠퍼스", "뉴스", "블로그"]:
                    score += 1000
                
                if dist < min_distance:
                    min_distance = dist
                    best_match = real_val
                    max_score = score
                elif dist == min_distance and score > max_score:
                    best_match = real_val
                    max_score = score
    except Exception as e:
        print(f"⚠️ 오타 교정 연산 실패: {e}")
        
    return best_match if best_match else keyword


# ==========================================
# [기능 고도화 2] 스마트 자연어 계산기 엔진
# ==========================================
def evaluate_math_expression_ai(keyword):
    numbers = re.findall(r'\d+', keyword)
    if not numbers:
        return None

    if GEMINI_API_KEY and GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        prompt = (
            f"사용자의 질문: '{keyword}'\n"
            "위 문장에서 숫자와 연산의 의도를 파악하여, 파이썬 eval() 함수로 즉시 계산 가능한 순수 수학 수식 한 줄로만 변환해줘.\n"
            "예시: '36000 나누기 3' -> 36000 / 3\n"
            "예시: '2를 8번 곱해줘' -> 2 ** 8\n"
            "주의: 설명이나 한글, 기호(`)를 전혀 붙이지 말고 오직 숫자와 파이썬 연산자(+, -, *, /, **)로만 구성된 한 줄만 출력해."
        )
        data = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            response = requests.post(url, headers=headers, json=data, timeout=3)
            if response.status_code == 200:
                ai_response = response.json()
                raw_expr = ai_response['contents'][0]['parts'][0]['text'].strip()
                clean_expr = raw_expr.replace("`", "").strip()
                if re.match(r'^[\d+\-*/().\s]+$', clean_expr):
                    result = eval(clean_expr)
                    return {"type": "ai_calculator", "user_input": keyword, "parsed_expression": clean_expr, "result": str(result)}
        except Exception:
            pass

    return fallback_math_expression(keyword)


def fallback_math_expression(keyword):
    """
    모든 한글 변형 어미와 수사를 유연하게 수식으로 정밀 파싱하는 엔진
    """
    numbers = re.findall(r'\d+', keyword)
    
    # 한글 수사 지원
    korean_num_map = {"둘이서": "2", "셋이서": "3", "네명이서": "4", "여섯이서": "6", "반띵": "2", "삼등분": "3"}
    for k_word, num_str in korean_num_map.items():
        if k_word in keyword and len(numbers) == 1:
            numbers.append(num_str)

    if len(numbers) >= 2:
        num1, num2 = numbers[0], numbers[1]
        
        op = None
        # 1. 거듭제곱 / 제곱 우선 판별 ("N번 곱", "제곱", "**")
        if any(w in keyword for w in ["번 곱", "번곱", "제곱", "거듭제곱", "**"]):
            op = "**"
        # 2. 일반 나누기
        elif any(w in keyword for w in ["나누", "나눠", "분", "쪼개", "N빵", "n빵", "/", "등분"]):
            op = "/"
        # 3. 일반 곱하기
        elif any(w in keyword for w in ["곱", "배", "*"]):
            op = "*"
        # 4. 더하기
        elif any(w in keyword for w in ["더", "합", "플러스", "+"]):
            op = "+"
        # 5. 빼기
        elif any(w in keyword for w in ["빼", "차", "마이너스", "-"]):
            op = "-"

        if op:
            parsed_expr = f"{num1} {op} {num2}"
            try:
                result = eval(parsed_expr)
                return {
                    "type": "smart_fallback_calculator",
                    "expression": keyword,
                    "parsed_expression": parsed_expr,
                    "result": str(result)
                }
            except Exception:
                return None
    return None


# ==========================================
# [기능 고도화 3] 글로벌 날씨 확장 엔진
# ==========================================
def get_realtime_weather(city_name="Seoul"):
    city_map = {
        "서울": "Seoul", "부산": "Busan", "인천": "Incheon", 
        "대구": "Daegu", "대전": "Daejeon", "광주": "Gwangju", 
        "울산": "Ulsan", "제주": "Jeju", "뉴욕": "New York", 
        "런던": "London", "도쿄": "Tokyo", "파리": "Paris",
        "베이징": "Beijing", "시드니": "Sydney", "로스앤젤레스": "Los Angeles", 
        "LA": "Los Angeles", "상하이": "Shanghai", "두바이": "Dubai", 
        "싱가포르": "Singapore", "방콕": "Bangkok"
    }
    
    target_city_en = city_map.get(city_name, "Seoul")
    target_city_ko = city_name if city_name in city_map else "서울시"

    if not OPENWEATHER_API_KEY or OPENWEATHER_API_KEY == "YOUR_API_KEY_HERE":
        return {
            "location": target_city_ko,
            "temperature": "18.5°C" if city_name == "뉴욕" else "26.2°C" if city_name == "도쿄" else "24.5°C",
            "status": "맑음 (Clear)" if city_name == "뉴욕" else "흐림 (Cloudy)",
            "humidity": "60%",
            "wind_speed": "2.4 m/s",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "source": f"Mock ({target_city_ko} 글로벌 매칭 완공)"
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
    except Exception:
        pass
    return {
        "location": target_city_ko,
        "temperature": "24.5°C",
        "status": "통신 제한 호환 모드",
        "humidity": "85%",
        "wind_speed": "3.2 m/s",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "source": "Fallback"
    }


def parse_weather_city(keyword):
    cities = [
        "서울", "부산", "인천", "대구", "대전", "광주", "울산", "제주", 
        "뉴욕", "런던", "도쿄", "파리", "베이징", "시드니", "상하이", 
        "두바이", "싱가포르", "방콕", "LA", "로스앤젤레스"
    ]
    for city in cities:
        if city in keyword:
            return city
    return "서울"


# ==========================================
# [기능 고도화 4] 실시간 인기 검색어 TOP 10 랭킹 집계
# ==========================================
def get_realtime_trending_keywords():
    try:
        trending_raw = r.zrevrange("saver:popular_scores", 0, 9, withscores=True)
        trending_list = []
        for rank, (kw, score) in enumerate(trending_raw, 1):
            if int(score) >= 2:
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
# [기능 1] 초성 분리 및 무한 DB 인덱서 엔진
# ==========================================
def get_jamo_string(text):
    return j2hcj(h2j(text))


def initialize_autocomplete_database():
    if r.exists("saver:autocomplete:jamo_map"):
        print("⚡ [초고속 가동] 기존 등록된 Redis 사전을 즉시 로드합니다.")
        return
        
    print("🔄 [시스템] 데이터베이스 전체 스캔 및 포털급 단어 사전 자동 빌드 중...")
    
    master_keywords = {
        "날씨": 1000, "오늘 날씨": 1000, "날씨 정보": 1000, 
        "블로그 포스트": 1000, "최신 뉴스": 1000, "계산기": 1000, 
        "기념일": 1000, "크리스마스": 1000, "학사": 1000, "캠퍼스": 1000
    }
    
    try:
        for kw, score in master_keywords.items():
            jamo_key = get_jamo_string(kw)
            r.hset("saver:autocomplete:jamo_map", jamo_key, kw)
            r.zadd("saver:popular_scores", {kw: score})
            
        pg_cursor.execute("SELECT title, content FROM hufspress UNION ALL SELECT title, content FROM blog")
        rows = pg_cursor.fetchall()
        
        unique_words = set()
        for title, content in rows:
            combined_text = f"{title if title else ''} {content if content else ''}"
            words = re.findall(r'[가-힣a-zA-Z0-9]{2,}', combined_text)
            for w in words:
                clean_w = re.sub(r'(은|는|이|가|을|를|에|와|과|에서|하고|이고)$', '', w)
                if len(clean_w) >= 2:
                    unique_words.add(clean_w)
        
        pipeline = r.pipeline()
        for word in unique_words:
            if word not in master_keywords:
                jamo_key = get_jamo_string(word)
                pipeline.hset("saver:autocomplete:jamo_map", jamo_key, word)
                pipeline.zadd("saver:popular_scores", {word: 1})
        pipeline.execute()
        
        print(f"✅ [색인 완공] DB 기반 무한 검색 사전 구축 완료 (총 {len(unique_words)}개 유효 명사 정밀 인덱싱됨).")
    except Exception as e:
        print(f"⚠️ 자동완성 사전 인덱서 가동 실패: {e}")


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
    current_date = datetime(2026, 7, 20)
    
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
            return {"target_id": "weather", "recommend_message": info["msg"]}
        for kw in info["keywords"]:
            if kw in keyword:
                return {"target_id": target_id, "recommend_message": info["msg"]}
    return None


# ==========================================
# Core Search Logic
# ==========================================
def get_saver_search_result(raw_keyword, client_ip="127.0.0.1"):
    if is_rate_limited(client_ip, limit=2, period=4):
        return {
            "error": "Too Many Requests",
            "message": "너무 빠른 검색 요청이 감지되었습니다. 잠시 후 다시 시도해 주세요 (4초 내 최대 2회 제한)."
        }

    # 1. 오타 교정 전에 자연어 연산부터 우선 파싱
    math_result = evaluate_math_expression_ai(raw_keyword)
    if math_result:
        start_time = time.time()
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

    normalized_keyword = normalize_and_synonym_filter(raw_keyword)
    keyword = correct_typo_fuzzy(normalized_keyword)
    
    if raw_keyword != keyword:
         print(f"\n[검색엔진 가동] 오타 교정 매칭: '{raw_keyword}' ➡️ '{keyword}' (IP: {client_ip})")
    else:
         print(f"\n[검색엔진 가동] 검색 키워드: '{keyword}' (IP: {client_ip})")
    
    start_time = time.time()
    
    cache_key = f"saver:cache:{keyword}"
    try:
        cached_data = r.get(cache_key)
        if cached_data:
            latency = (time.time() - start_time) * 1000
            result_json = json.loads(cached_data)
            result_json["SAVER_Special_Search"]["검색속도"] = f"{latency:.2f}ms (Cache Hit)"
            r.zincrby("saver:popular_scores", 1, keyword)
            return result_json
    except Exception:
        pass

    # 2. 의도 분석 처리 및 날씨/디데이 바인딩
    user_intent = detect_user_intent(keyword)

    if user_intent:
        realtime_widget_data = None
        if user_intent["target_id"] == "weather":
            target_city = parse_weather_city(keyword)
            realtime_widget_data = get_realtime_weather(target_city)
        elif user_intent["target_id"] == "anniversary":
            realtime_widget_data = calculate_anniversary_dday(keyword)

        latency = (time.time() - start_time) * 1000
        related_keywords = search_autocomplete(keyword)
        feedback_message = f"'{raw_keyword}'(으)로 입력된 오타를 교정하여 '{keyword}'의 결과를 보여줍니다." if raw_keyword != keyword else None

        output = {
            "SAVER_Special_Search": {
                "검색속도": f"{latency:.2f}ms",
                "타입": "recommend",
                "오타교정_안내": feedback_message,
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
        try:
            r.set(cache_key, json.dumps(output), ex=60)
        except Exception:
            pass
        return output

    # 3. 일반 키워드 자동완성 탐색
    related_keywords = search_autocomplete(keyword)
    if not related_keywords:
        related_keywords = [f"{keyword} 추천", f"{keyword} 최신 뉴스", f"HUFS {keyword}"]

    # 4. PostgreSQL 통합 검색
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
            r.zincrby("saver:popular_scores", 1, keyword)
            r.hset("saver:autocomplete:jamo_map", get_jamo_string(keyword), keyword)
        else:
            best_result = {
                "게시처": "None",
                "제목": f"'{keyword}'에 대한 검색 결과가 없습니다.",
                "요약본(100자)": "데이터베이스 내에 매칭되는 본문이 없습니다."
            }
    except Exception as e:
        print(f"❌ [디비 에러] {e}")

    latency = (time.time() - start_time) * 1000
    feedback_message = f"'{raw_keyword}'(으)로 입력된 오타를 교정하여 '{keyword}'의 결과를 보여줍니다." if raw_keyword != keyword else None

    output = {
        "SAVER_Special_Search": {
            "검색속도": f"{latency:.2f}ms",
            "타입": "search",
            "오타교정_안내": feedback_message,
            "최선의_결과": best_result,
            "추천_결과": user_intent,
            "연관_검색어_추천": related_keywords
        }
    }
    try:
        if best_result and best_result["게시처"] != "None":
            r.set(cache_key, json.dumps(output), ex=60)
    except Exception:
        pass

    return output


if __name__ == "__main__":
    print("=" * 60)
    print("⚡ SAVER Search Engine v5.8 (Universal Math Parser)")
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
                ranking = get_realtime_trending_keywords()
                print("\n🔥 [SAVER 실시간 인기 검색어 TOP 10] 🔥")
                for rank_item in ranking:
                    print(f"[{rank_item['순위']}위] {rank_item['키워드']} (검색 수: {rank_item['검색횟수']}회)")
                print("-"*40)
                continue
            
            search_keyword = menu if menu != '1' else input("🔍 검색 키워드 입력: ").strip()
            if not search_keyword:
                continue
                
            final_output = get_saver_search_result(search_keyword, client_ip="127.0.0.1")
            
            if final_output:
                print("\n" + "="*20 + " [프론트엔드 전달 API 응답 예시] " + "="*20)
                print(json.dumps(final_output, indent=4, ensure_ascii=False))
                print("="*64)
    finally:
        if pg_cursor:
            pg_cursor.close()
        if pg_conn:
            pg_conn.close()