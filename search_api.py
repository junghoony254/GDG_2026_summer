import re
import redis
import psycopg2
import json
import time
import requests
from datetime import datetime, timedelta, timezone
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
# [알고리즘] 레벤슈타인 편집 거리 알고리즘
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


def get_jamo_string(text):
    return j2hcj(h2j(text))


# ==========================================
# [신규 통합 엔진 1] 환율 계산 모듈 (원화 -> 타 통화 동적 환산 보강)
# ==========================================
def evaluate_currency_converter(keyword):
    # '파운드' 키워드가 들어왔을 때 '무게'나 'lbs' 맥락이면 환율에서 처리하지 않음
    if "파운드" in keyword and any(w in keyword for w in ["무게", "lbs", "kg", "킬로"]):
        return None

    currency_patterns = r'(\d+(?:\.\d+)?)\s*(달러|엔|유로|위안|원|영국\s*파운드|파운드|gbp|usd|jpy|eur|cny)'
    matches = re.findall(currency_patterns, keyword.lower())
    
    if not matches:
        return None

    rates = {
        "달러": 1380.0, "usd": 1380.0,
        "엔": 9.2, "jpy": 9.2,
        "유로": 1500.0, "eur": 1500.0,
        "위안": 190.0, "cny": 190.0,
        "파운드": 1780.0, "영국 파운드": 1780.0, "gbp": 1780.0
    }

    val, unit = matches[0]
    val = float(val)

    # 파운드 -> 달러 환산 특수 처리 ("225파운드를 달러로 바꿔줘")
    if ("파운드" in unit or "gbp" in unit) and "달러" in keyword:
        krw_val = val * rates["파운드"]
        usd_val = krw_val / rates["달러"]
        return {
            "type": "currency_converter",
            "input": keyword,
            "result_text": f"{val:,.2f} GBP (파운드) = 약 {usd_val:,.2f} USD (달러)",
            "base_rate_info": "하나은행 실시간 매매기준율 호환 모드"
        }

    if unit in ["엔", "jpy"]:
        krw_val = val * rates[unit]
        result_str = f"{val:,.0f}엔 = 약 {krw_val:,.0f}원 (KRW)"
    elif unit in ["원"]:
        # [수정 완공] 원화 -> 입력 문장 내 타겟 통화(파운드, 엔, 유로, 달러 등) 분기 연산
        target_unit = "달러"
        if "파운드" in keyword or "gbp" in keyword:
            target_unit = "파운드"
        elif "엔" in keyword or "jpy" in keyword:
            target_unit = "엔"
        elif "유로" in keyword or "eur" in keyword:
            target_unit = "유로"
        elif "위안" in keyword or "cny" in keyword:
            target_unit = "위안"
        elif "달러" in keyword or "usd" in keyword:
            target_unit = "달러"

        target_rate = rates[target_unit]
        if target_unit in ["엔", "jpy"]:
            calc_val = val / target_rate
            result_str = f"{val:,.0f}원 = 약 {calc_val:,.0f}엔 (JPY)"
        else:
            calc_val = val / target_rate
            unit_display = "GBP (파운드)" if target_unit == "파운드" else f"{target_unit.upper()}"
            result_str = f"{val:,.0f}원 = 약 {calc_val:,.2f} {unit_display}"
    elif unit in rates:
        krw_val = val * rates[unit]
        result_str = f"{val:,.2f} {unit.upper()} = 약 {krw_val:,.0f}원 (KRW)"
    else:
        return None

    return {
        "type": "currency_converter",
        "input": keyword,
        "result_text": result_str,
        "base_rate_info": "하나은행 실시간 매매기준율 호환 모드"
    }


# ==========================================
# [신규 통합 엔진 2] 단위 변환기 모듈
# ==========================================
def evaluate_unit_converter(keyword):
    # 통화 정밀 검사
    currency_explicit = ["환율", "달러", "유로", "엔화", "위안화", "영국파운드", "gbp", "usd", "eur", "jpy", "cny"]
    has_explicit_currency = any(w in keyword.lower() for w in currency_explicit)
    has_krw = bool(re.search(r'\d+\s*원', keyword)) or "원화" in keyword

    if has_explicit_currency or has_krw:
        return None

    cm_match = re.search(r'(\d+(?:\.\d+)?)\s*cm', keyword, re.IGNORECASE)
    inch_match = re.search(r'(\d+(?:\.\d+)?)\s*(인치|inch)', keyword, re.IGNORECASE)
    kg_match = re.search(r'(\d+(?:\.\d+)?)\s*(kg|킬로그램|킬로)', keyword, re.IGNORECASE)
    lbs_match = re.search(r'(\d+(?:\.\d+)?)\s*(파운드|lbs)', keyword, re.IGNORECASE)
    pyung_match = re.search(r'(\d+(?:\.\d+)?)\s*평', keyword)
    m2_match = re.search(r'(\d+(?:\.\d+)?)\s*(m2|제곱미터)', keyword, re.IGNORECASE)
    yard_match = re.search(r'(\d+(?:\.\d+)?)\s*(야드|yard|yd)', keyword, re.IGNORECASE)
    meter_match = re.search(r'(\d+(?:\.\d+)?)\s*(미터|m)(?!2)', keyword, re.IGNORECASE)

    # 야드 -> 미터
    if yard_match and ("미터" in keyword or "m" in keyword or "변환" in keyword or "바꿔" in keyword):
        val = float(yard_match.group(1))
        res = val * 0.9144
        return {"type": "unit_converter", "converted": f"{val} yard = {res:.2f} m"}

    # 미터 -> 야드
    if meter_match and ("야드" in keyword or "yard" in keyword or "변환" in keyword or "바꿔" in keyword):
        val = float(meter_match.group(1))
        res = val / 0.9144
        return {"type": "unit_converter", "converted": f"{val} m = {res:.2f} yard"}

    # cm -> inch
    if cm_match and ("인치" in keyword or "inch" in keyword or "변환" in keyword or "바꿔" in keyword):
        val = float(cm_match.group(1))
        res = val / 2.54
        return {"type": "unit_converter", "converted": f"{val} cm = {res:.2f} inch"}

    # inch -> cm
    if inch_match and ("cm" in keyword or "센티" in keyword or "변환" in keyword or "바꿔" in keyword):
        val = float(inch_match.group(1))
        res = val * 2.54
        return {"type": "unit_converter", "converted": f"{val} inch = {res:.2f} cm"}

    # kg / 킬로그램 -> lbs / 파운드
    if kg_match and ("파운드" in keyword or "lbs" in keyword or "변환" in keyword or "바꿔" in keyword):
        val = float(kg_match.group(1))
        res = val * 2.20462
        return {"type": "unit_converter", "converted": f"{val} kg = {res:.2f} lbs"}

    # lbs / 파운드 -> kg / 킬로그램 (역방향)
    if lbs_match and ("kg" in keyword or "킬로" in keyword or "무게" in keyword or "변환" in keyword or "lbs" in keyword or "파운드" in keyword or "바꿔" in keyword):
        val = float(lbs_match.group(1))
        res = val * 0.453592
        return {"type": "unit_converter", "converted": f"{val} lbs = {res:.2f} kg"}

    # 평 -> m²
    if pyung_match:
        val = float(pyung_match.group(1))
        res = val * 3.30579
        return {"type": "unit_converter", "converted": f"{val}평 = {res:.2f} m²"}

    # m² -> 평
    if m2_match:
        val = float(m2_match.group(1))
        res = val / 3.30579
        return {"type": "unit_converter", "converted": f"{val} m² = {res:.2f} 평"}

    return None


# ==========================================
# [기능 고도화] 스마트 자연어 계산기 엔진
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
    numbers = re.findall(r'\d+', keyword)
    korean_num_map = {"둘이서": "2", "셋이서": "3", "네명이서": "4", "여섯이서": "6", "반띵": "2", "삼등분": "3"}
    for k_word, num_str in korean_num_map.items():
        if k_word in keyword and len(numbers) == 1:
            numbers.append(num_str)

    if len(numbers) >= 2:
        num1, num2 = numbers[0], numbers[1]
        op = None
        if any(w in keyword for w in ["번 곱", "번곱", "제곱", "거듭제곱", "**"]):
            op = "**"
        elif any(w in keyword for w in ["나누", "나눠", "분", "쪼개", "N빵", "n빵", "/", "등분"]):
            op = "/"
        elif any(w in keyword for w in ["곱", "배", "*"]):
            op = "*"
        elif any(w in keyword for w in ["더", "합", "플러스", "+"]):
            op = "+"
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
# [기능 고도화] 세계 시간 / 시차 연산 엔진
# ==========================================
def get_world_time(city_name="서울"):
    tz_map = {
        "서울": 9, "부산": 9, "인천": 9, "대구": 9, "대전": 9, "광주": 9, "제주": 9,
        "도쿄": 9, "베이징": 8, "상하이": 8, "싱가포르": 8, "방콕": 7,
        "두바이": 4, "파리": 2, "런던": 1, "뉴욕": -4, "LA": -7, "로스앤젤레스": -7, "시드니": 10
    }
    target_city = city_name if city_name in tz_map else "서울"
    target_offset = tz_map[target_city]
    
    utc_now = datetime.now(timezone.utc)
    target_time = utc_now + timedelta(hours=target_offset)
    time_diff = target_offset - 9
    diff_str = "한국과 동일" if time_diff == 0 else f"한국보다 {abs(time_diff)}시간 " + ("빠름" if time_diff > 0 else "느림")
    
    weekday_map = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    weekday_str = weekday_map[target_time.weekday()]

    return {
        "location": target_city,
        "current_time": target_time.strftime("%Y-%m-%d %H:%M:%S") + f" ({weekday_str})",
        "timezone": f"UTC{'+' if target_offset >= 0 else ''}{target_offset}",
        "time_difference": diff_str
    }


# ==========================================
# [기능 고도화] 글로벌 날씨 확장 엔진
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


def parse_city_name(keyword):
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
# [기능 고도화] 실시간 인기 검색어 TOP 10 랭킹 집계
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
def initialize_autocomplete_database():
    if r.exists("saver:autocomplete:jamo_map"):
        print("⚡ [초고속 가동] 기존 등록된 Redis 사전을 즉시 로드합니다.")
        return
        
    print("🔄 [시스템] 데이터베이스 전체 스캔 및 포털급 단어 사전 자동 빌드 중...")
    
    master_keywords = {
        "날씨": 1000, "오늘 날씨": 1000, "날씨 정보": 1000, 
        "블로그 포스트": 1000, "최신 뉴스": 1000, "계산기": 1000, 
        "기념일": 1000, "크리스마스": 1000, "학사": 1000, "캠퍼스": 1000, "시간": 1000
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


def normalize_and_synonym_filter(keyword):
    clean_kw = keyword.strip()
    unit_preserve_keywords = ["시간", "몇시", "시차", "현재", "달러", "환율", "평", "cm", "kg", "lbs", "파운드", "야드", "미터", "인치"]
    if not any(w in clean_kw for w in unit_preserve_keywords):
        clean_kw = re.sub(r'(은|는|이|가|을|를|의|에|어때|언제야|현재|정보|날짜|디데이|d-day)$', '', clean_kw)
    clean_kw = clean_kw.strip()
    return clean_kw


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
                if real_val in ["날씨", "크리스마스", "학사", "캠퍼스", "뉴스", "블로그", "시간", "환율"]:
                    score += 1000
                if dist < min_distance:
                    min_distance = dist
                    best_match = real_val
                    max_score = score
                elif dist == min_distance and score > max_score:
                    best_match = real_val
                    max_score = score
    except Exception:
        pass
    return best_match if best_match else keyword


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


def calculate_anniversary_dday(keyword):
    anniversaries = {
        "크리스마스": "2026-12-25", "성탄절": "2026-12-25",
        "광복절": "2026-08-15", "추석": "2026-09-25",
        "한글날": "2026-10-09", "신정": "2027-01-01", "새해": "2027-01-01"
    }
    weekday_map = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    target_event = next((k for k in anniversaries if k in keyword), None)
    if not target_event:
        return None

    target_date = datetime.strptime(anniversaries[target_event], "%Y-%m-%d")
    current_date = datetime(2026, 7, 21)
    days_left = (target_date - current_date).days
    
    return {
        "event_name": target_event,
        "date": f"{anniversaries[target_event]} ({weekday_map[target_date.weekday()]})",
        "d_day": f"D-{days_left}" if days_left > 0 else "D-Day",
        "message": f"{target_event}까지 {days_left}일 남았습니다!"
    }


def detect_user_intent(keyword):
    intent_map = {
        "blog": {"keywords": ["블로그", "글", "포스트"], "msg": "블로그 탭에서 후기를 확인해보세요!"},
        "weather": {"keywords": ["날씨", "기온", "비", "온도", "ㄴㅆ"], "msg": "날씨 탭에서 실시간 전국 기상을 확인해보세요!"},
        "time": {"keywords": ["시간", "현재 시간", "현재시간", "몇시", "시차"], "msg": "시간 탭에서 실시간 도시 시각을 확인해보세요!"},
        "news": {"keywords": ["뉴스", "기사", "소식"], "msg": "뉴스 탭에서 최신 뉴스를 확인해보세요!"},
        "anniversary": {"keywords": ["크리스마스", "성탄절", "광복절", "추석", "디데이"], "msg": "기념일 디데이를 확인해보세요!"}
    }
    for target_id, info in intent_map.items():
        if any(kw in keyword for kw in info["keywords"]):
            return {"target_id": target_id, "recommend_message": info["msg"]}
    return None


# ==========================================
# Core Search Logic (통합 라우터)
# ==========================================
def get_saver_search_result(raw_keyword, client_ip="127.0.0.1"):
    if is_rate_limited(client_ip, limit=2, period=4):
        return {"error": "Too Many Requests", "message": "요청이 너무 빠급니다. 잠시 후 다시 시도해 주세요."}

    start_time = time.time()

    # 0. IP / 네트워크 확인 특수 키워드
    if any(w in raw_keyword.lower() for w in ["내 ip", "아이피", "ip 확인", "network info"]):
        return {
            "SAVER_Special_Search": {
                "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
                "타입": "network_info",
                "결과": {"client_ip": client_ip, "status": "Connected", "latency": "0.15ms"}
            }
        }

    # 1. 계산기 키워드(n빵, 나누기, 수식 등)가 감지되면 수학 계산기를 최우선 실행
    calc_override_keywords = ["n빵", "N빵", "나누", "나눠", "더치페이", "등분", "반띵", "삼등분", "번 곱", "제곱"]
    if any(w in raw_keyword for w in calc_override_keywords):
        math_result = evaluate_math_expression_ai(raw_keyword)
        if math_result:
            return {
                "SAVER_Special_Search": {
                    "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
                    "타입": "calculator",
                    "결과": math_result
                }
            }

    # 2. 환율 계산기 특수 연산
    curr_result = evaluate_currency_converter(raw_keyword)
    if curr_result:
        return {
            "SAVER_Special_Search": {
                "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
                "타입": "currency",
                "결과": curr_result
            }
        }

    # 3. 단위 변환기 특수 연산
    unit_result = evaluate_unit_converter(raw_keyword)
    if unit_result:
        return {
            "SAVER_Special_Search": {
                "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
                "타입": "unit_converter",
                "결과": unit_result
            }
        }

    # 4. 일반 자연어 수식 계산기 fallback
    math_result = evaluate_math_expression_ai(raw_keyword)
    if math_result:
        return {
            "SAVER_Special_Search": {
                "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
                "타입": "calculator",
                "결과": math_result
            }
        }

    # 5. 키워드 오타 정규화 및 캐시 확인
    keyword = correct_typo_fuzzy(normalize_and_synonym_filter(raw_keyword))
    cache_key = f"saver:cache:{keyword}"
    try:
        cached_data = r.get(cache_key)
        if cached_data:
            res = json.loads(cached_data)
            res["SAVER_Special_Search"]["검색속도"] = f"{(time.time() - start_time)*1000:.2f}ms (Cache Hit)"
            r.zincrby("saver:popular_scores", 1, keyword)
            return res
    except Exception:
        pass

    # 6. 의도 분석 (날씨 / 시간 / 디데이 / 뉴스)
    user_intent = detect_user_intent(keyword)
    if user_intent:
        target_city = parse_city_name(keyword)
        realtime_data = None
        if user_intent["target_id"] == "weather":
            realtime_data = get_realtime_weather(target_city)
        elif user_intent["target_id"] == "time":
            realtime_data = get_world_time(target_city)
        elif user_intent["target_id"] == "anniversary":
            realtime_data = calculate_anniversary_dday(keyword)

        output = {
            "SAVER_Special_Search": {
                "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
                "타입": "recommend",
                "최선의_결과": {"게시처": "Widget", "제목": f"{user_intent['target_id'].upper()} 실시간 매칭"},
                "추천_결과": {"target_id": user_intent["target_id"], "realtime_data": realtime_data},
                "연관_검색어_추천": search_autocomplete(keyword)
            }
        }
        try: 
            r.set(cache_key, json.dumps(output), ex=60)
            r.zincrby("saver:popular_scores", 1, keyword)
        except Exception: pass
        return output

    # 7. PostgreSQL DB 통합 검색
    best_result = None
    try:
        query = "SELECT 'hufspress', title, content FROM hufspress WHERE title LIKE %s OR content LIKE %s UNION ALL SELECT 'blog', title, content FROM blog WHERE title LIKE %s OR content LIKE %s LIMIT 1;"
        p = f"%{keyword}%"
        pg_cursor.execute(query, (p, p, p, p))
        row = pg_cursor.fetchone()
        if row:
            best_result = {"게시처": row[0], "제목": row[1], "요약본": (row[2] or "")[:100] + "..."}
            r.zincrby("saver:popular_scores", 1, keyword)
            r.hset("saver:autocomplete:jamo_map", get_jamo_string(keyword), keyword)
        else:
            best_result = {"게시처": "None", "제목": f"'{keyword}' 검색 결과가 없습니다."}
    except Exception:
        best_result = {"게시처": "None", "제목": "DB 조회 에러"}

    output = {
        "SAVER_Special_Search": {
            "검색속도": f"{(time.time() - start_time)*1000:.2f}ms",
            "타입": "search",
            "최선의_결과": best_result,
            "연관_검색어_추천": search_autocomplete(keyword)
        }
    }
    return output


# ==========================================
# CLI 메뉴 인터페이스
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("⚡ SAVER Search Engine v6.0 (All-In-One Smart Edition)")
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
                if ranking:
                    for rank_item in ranking:
                        print(f"[{rank_item['순위']}위] {rank_item['키워드']} (검색 수: {rank_item['검색횟수']}회)")
                else:
                    print("현재 집계된 인기 검색어 데이터가 없습니다.")
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
        if pg_cursor: pg_cursor.close()
        if pg_conn: pg_conn.close()