from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
# search_api.py에서 작성한 핵심 엔진 함수 불러오기
from search_api import get_saver_search_result

app = FastAPI(title="SAVER Search API Engine")

# CORS 설정 (프론트엔드 연동용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/search")
def search_endpoint(q: str, request: Request):
    client_ip = request.client.host
    return get_saver_search_result(q, client_ip=client_ip)

@app.get("/health")
def health_check():
    return {"status": "ok"}