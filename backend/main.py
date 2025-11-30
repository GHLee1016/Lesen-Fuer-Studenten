from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Header, Query, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional, Tuple 
import io, csv, json, time, base64
from pathlib import Path

# =============================================
# ========== [신규] 보안 및 AI 라이브러리 ==========
# =============================================
import hashlib
import secrets

try:
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO
    print("OpenCV, NumPy, PyTorch, and YOLO loaded successfully.")
except ImportError:
    print("ERROR: AI libraries not found.")
    print("Please run: pip install opencv-python-headless numpy torch torchvision ultralytics")
    cv2 = None
    np = None
    torch = None
    YOLO = None
# =============================================

ART_DIR = Path(__file__).parent / "articles"

app = FastAPI()
           
# =============================================
# ========== [수정] CORS 설정 ==========
# =============================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://localhost:8080",
        "https://127.0.0.1:8080",
        "https://localhost:8443",
        "https://127.0.0.1:8443",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://lesen-fuer-studenten.onrender.com",
        "https://ghlee1016.github.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    # [수정] "X-Admin-Token" 커스텀 헤더를 명시적으로 허용
    allow_headers=["*", "X-Admin-Token"],
)
# =============================================

# 세션 메모리
SESSIONS: Dict[str, Dict] = {}
CLIENTS: Dict[str, WebSocket] = {}

# 토큰 해시
ADMIN_TOKEN_HASH = "7677c1e67d477f43131129b8ce3ad62e2d84b1ec1eb74f81fd5457e8fac07d79"

ADMIN_CLIENTS: List[WebSocket] = []
LEVEL_CFG = {"high": 70.0, "low": 40.0}

YOLO_MODEL = None
DEVICE = None
if torch and YOLO and cv2:
    if torch.backends.mps.is_available():
        DEVICE = torch.device("mps")
    elif torch.cuda.is_available():
        DEVICE = torch.device("cuda")
    else:
        DEVICE = torch.device("cpu")

    print(f"Loading AI models onto device: {DEVICE}")
    
    try:
        YOLO_MODEL = YOLO("yolov8n-pose.pt").to(DEVICE)
        print("YOLOv8-Pose model loaded successfully.")
    except Exception as e:
        print(f"Error loading YOLO model: {e}")
        YOLO_MODEL = None
else:
    print("AI libraries not loaded, skipping model setup.")

def verify_token(token: str) -> bool:
    """입력된 토큰을 해시하여 저장된 해시와 안전하게 비교합니다."""
    if not token:
        return False
    # 입력된 토큰을 동일한 방식으로 해시
    incoming_hash = hashlib.sha256(token.encode()).hexdigest()
    # secrets.compare_digest를 사용하여 타이밍 공격에 안전하게 비교
    return secrets.compare_digest(incoming_hash, ADMIN_TOKEN_HASH)

def require_admin(x_admin_token: str = Header(None)):
    """HTTP 헤더 토큰을 검증하는 의존성."""
    # ▼▼▼ [추가] 감시 로그 ▼▼▼
    print(f"🔍 [DEBUG] 받은 토큰: {x_admin_token}")
    
    if not verify_token(x_admin_token):
        print(f"❌ [DEBUG] 토큰 검증 실패!") # 추가
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid admin token")

def ensure_session(user_id: str):
    if user_id not in SESSIONS:
        SESSIONS[user_id] = {
            "points": [], 
            "scores": [], 
            "variant": "b1",
            "last_score": 0.0   # 가장 최근에 받은 점수 (기본값 0)
        }

def groups_by_level(high=None, low=None):
    if high is None: high = LEVEL_CFG["high"]
    if low  is None: low  = LEVEL_CFG["low"]
    groups = {"high": [], "medium": [], "low": []}
    for uid, sess in SESSIONS.items():
        score = sess.get("last_score", 0.0) 
        
        if score >= high: lv = "high"
        elif score < low: lv = "low"
        else: lv = "medium"
        
        groups[lv].append({
            "user_id": uid, 
            "last_score": round(score, 1)
        })
        
    for k in groups:
        groups[k].sort(key=lambda x: x["last_score"], reverse=True)
    return groups

async def broadcast_admin(payload: dict):
    dead = []
    for ws in ADMIN_CLIENTS:
        try: await ws.send_json(payload)
        except: dead.append(ws)
    for d in dead:
        try: ADMIN_CLIENTS.remove(d)
        except: pass

async def broadcast_users():
    payload = {"type": "users", "users": list(CLIENTS.keys())}
    dead = []
    for uid, ws in list(CLIENTS.items()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(uid)
    for uid in dead:
        try: CLIENTS.pop(uid, None)
        except: pass

def detect_gaze_yolo(img_bytes: bytes) -> Tuple[Optional[float], Optional[float]]:
    """Numpy/OpenCV/YOLOv8을 사용해 시선 좌표를 감지합니다."""
    if not YOLO_MODEL or not np or not cv2:
        return None, None
    
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None, None
        
        results = YOLO_MODEL(img, verbose=False, device=DEVICE)
        
        if not results or not results[0].keypoints:
            return None, None
            
        keypoints = results[0].keypoints.xy.cpu().numpy()
        if len(keypoints) == 0:
            return None, None
        
        kpts = keypoints[0] # 첫 번째 사람
        
        left_eye_x, left_eye_y = kpts[1]
        right_eye_x, right_eye_y = kpts[2]
        
        img_width = img.shape[1]
        gaze_x = img_width - (left_eye_x + right_eye_x) / 2
        gaze_y = (left_eye_y + right_eye_y) / 2
            
        # [수정] float32를 float으로 형변환하여 JSON 오류 해결
        return float(gaze_x), float(gaze_y)
        
    except Exception as e:
        print(f"[YOLOv8 Error] {e}")
        return None, None


# 기사 소스 (변경 없음)
ARTICLE_MAP = {
    "b1": """<h1>Achtung, schwarze Katze!?</h1>
<p>An Halloween sieht man sie überall: schwarze Katzen. Viele Menschen denken, dass sie Ungück bringen. Aber in manchen Ländern ist es genau anders herum. Wie kam das und was bedeutet das heute noch für diese Tiere?</p>
<p>Am 31. Oktober, an Halloween, sehen wir schwarze Katzen vor allem als Deko oder Kostüm. Ihr dunkles Fell und die geheimnisvollen Augen wirken auf viele Menschen etwas gruselig. Wenn eine schwarze Katze vor einem über die Straße läuft, denken viele, dass etwas Schlechtes passiert. Aber schon lange vor Halloween haben Menschen schwarze Katzen mit Angst und dem Bösen verbunden. Halloween wurde erst im 19. Jahrhundert in den USA bekannt. Im Mittelalter dachten die Menschen, schwarze Katzen gehören zum Teufel. Sie brachten sie mit Hexen zusammen. Viele schwarze Katzen wurden damals verfolgt and burned. That happened in some places even up to the 18th century.</p>
<p>Aber nicht überall bringen schwarze Katzen Unglück. Im alten Ägypten sahen die Menschen sie als heilig an. Sie verehrten die Göttin Bastet, die schwangere Frauen, Kinder und Mütter beschützt. In Großbritannien und Irland soll es Glück bringen, wenn man einer schwarzen Katze begegnet. In Japan sollen sie vor Krankheiten schützen. Außerdem sollen sie Frauen bei der Liebe helfen. Auch in Filmen und Serien gibt es sie als Schutz-Symbol. Ein Beispiel ist "Luna" aus der japanischen Serie "Sailor Moon". Das ist eine sprechende schwarze Katze, die die Heldinnen beschützt.</p>
<p>Für die schwarze Farbe des Fells ist ein bestimmtes Gen verantwortlich. Es heißt B-Gen. Dieses Gen macht ein dunkles Farbmittel. Dadurch wird das Fell, oft auch Nase und Pfoten, schwarz. Schwarze Katzen sind auch öfter männlich. Das liegt daran, dass das Gen auf einem bestimmten Chromosom liegt, dem X-Chroms-o-om. Wissenschaftler haben auch herausgefunden, dass dieses Gen die Tiere besser vor Krankheiten schützt. Außerdem ist das dunkle Fell nützlich, wenn die Katzen nachts Mäuse jagen.</p>
<p>Die dunkle Farbe kann aber auch ein Problem sein. Das passiert besonders dann, wenn diese Katzen ein neues Zuhause suchen. Der Deutsche Tierschutzbund hat 2020 eine Umfrage gemacht. 48 Prozent der Tierheime sagten, dass schwarze Katzen schwerer ein neues Zuhause finden. Viele Menschen finden sie nicht so schön. Außerdem kann man sie nicht so gut für Fotos in sozialen Netzwerken fotografieren. Nur an Halloween interessieren sich mehr Menschen für sie. Aber gerade dann wollen viele Tierheime keine schwarzen Katzen vermitteln. Sie wollen die Tiere schützen. Sie sollten nicht als Deko benutzt werden oder sogar für seltsame Rituale missbraucht werden.</p>
"""
}

@app.get("/")
def read_root():
    # Render가 서버 살았는지 체크할 때 404가 안 뜨게 해줍니다.
    return {"status": "ok", "message": "Backend is running!"}

@app.get("/article")
def get_article(v: Optional[str] = Query("b1")):
    # 프론트엔드에서 기사 내용을 요청하면 여기로 옵니다.
    html = ARTICLE_MAP.get(v or "b1", ARTICLE_MAP["b1"])
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    user_id = ws.query_params.get("user_id")
    variant = ws.query_params.get("variant", "b1") 
    await ws.accept()
    if not user_id:
        await ws.close(code=4400)
        return
    CLIENTS[user_id] = ws
    ensure_session(user_id)
    SESSIONS[user_id]["variant"] = variant 
    try:
        await broadcast_users()
        await broadcast_admin({"type": "groups", "data": groups_by_level()})
        while True:
            msg = await ws.receive()
            
            # ▼▼▼ [수정] 데이터 처리 중 에러가 나도 서버가 죽지 않게 보호합니다 ▼▼▼
            try:
                if msg.get("type") == "websocket.disconnect":
                    break

                if 'text' in msg:
                    data = json.loads(msg['text'])
                    typ = data.get("type")
                elif 'bytes' in msg:
                    typ = "video_frame_bytes"
                    data = msg['bytes']
                else:
                    continue

                if typ == "gaze":
                    t = int(data.get("t", 0))
                    x = float(data.get("x", 0))
                    y = float(data.get("y", 0))
                    SESSIONS[user_id]["points"].append((t, x, y))
                
                elif typ == "score":
                    # [안전장치] 값이 없거나(None) 이상하면 0.0으로 처리
                    t = int(data.get("t", 0))
                    raw_score = data.get("score")
                    
                    if raw_score is None: s = 0.0
                    else: s = float(raw_score) # 여기서 에러나면 except로 넘어감

                    print(f"📢 [DEBUG] 점수 수신: User={user_id}, Score={s}") # 로그 확인용

                    SESSIONS[user_id]["scores"].append(data) 
                    SESSIONS[user_id]["last_score"] = s
                    
                    # 관리자 페이지 갱신
                    print(f"📢 [DEBUG] 관리자에게 브로드캐스트 시도: {len(ADMIN_CLIENTS)}명")
                    await broadcast_admin({"type": "groups", "data": groups_by_level()})
                
                elif typ == "video_frame_bytes":
                    gaze_x, gaze_y = detect_gaze_yolo(data)
                    
                    if gaze_x is not None and gaze_y is not None:
                        await ws.send_json({
                            "type": "server_gaze",
                            "x": gaze_x,
                            "y": gaze_y,
                            "conf": 0.9, 
                            "t": int(time.time()*1000)
                        })

            except Exception as e:
                # 데이터 처리 중 에러가 나면 서버를 끄지 말고 로그만 남깁니다.
                print(f"⚠️ [Error] 데이터 처리 실패 ({user_id}): {e}")
                # continue를 통해 다음 메시지를 기다립니다.
                continue 
            # ▲▲▲ 보호 구역 끝 ▲▲▲

    except (WebSocketDisconnect, RuntimeError):
        pass

    finally:
        CLIENTS.pop(user_id, None)
        await broadcast_users()
        await broadcast_admin({"type": "groups", "data": groups_by_level()})

@app.websocket("/admin/ws")
async def admin_ws(ws: WebSocket, token: str = Query(None)): 
    
    if not verify_token(token):
        await ws.accept()
        await ws.close(code=4401, reason="Invalid admin token")
        return

    await ws.accept()
    ADMIN_CLIENTS.append(ws)
    await ws.send_json({"type": "groups", "data": groups_by_level()})
    try:
        while True:
            await ws.receive_text()  # keepalive
    except WebSocketDisconnect:
        pass
    finally:
        try: ADMIN_CLIENTS.remove(ws)
        except: pass

@app.get("/users")
def get_users():
    return {"users": list(CLIENTS.keys())}

@app.get("/export/{user_id}.csv")
def export_csv(user_id: str):
    sess = SESSIONS.get(user_id)
    if not sess:
        return JSONResponse({"error":"no data"}, status_code=404)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["t_ms","x_px","y_px"])
    for t,x,y in sess["points"]:
        w.writerow([t,x,y])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{user_id}.csv"'}
    )

@app.get("/export/{user_id}.json")
def export_json(user_id: str):
    sess = SESSIONS.get(user_id)
    if not sess:
        return JSONResponse({"error":"no data"}, status_code=404)
    
    scores_data = []
    for item in sess.get("scores", []):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            scores_data.append({"t": item[0], "score": item[1]})
        elif isinstance(item, dict):
            scores_data.append({
                "t": item.get("t"),
                "score": item.get("score"),
                "gaze_score": item.get("gaze_score"),
                "quiz_score": item.get("quiz_score"),
            })

    payload = {
        "user_id": user_id,
        "variant": sess.get("variant","b1"),
        "last_score": sess.get("last_score", 0.0), 
        "points": [{"t":t,"x":x,"y":y} for t,x,y in sess["points"]],
        "scores": scores_data,
        "exported_at": int(time.time()*1000),
    }
    data = json.dumps(payload, ensure_ascii=False)
    return StreamingResponse(iter([data]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{user_id}.json"'}
    )

# =============================================
# ========== [수정] export CSV/JSON 엔드포인트 ==========
# =============================================
@app.get("/admin/levels")
def admin_levels(_: None = Depends(require_admin)):
    return groups_by_level()

@app.get("/admin/levels/export.csv")
def admin_levels_csv(token: str = Query(None)): # Query 파라미터로 토큰 받기
    if not verify_token(token):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid admin token")
        
    groups = groups_by_level()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["level","user_id","last_score"])
    for lv, items in groups.items():
        for it in items:
            w.writerow([lv, it["user_id"], it.get("last_score", 0.0)])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="levels.csv"'}
    )

@app.get("/admin/levels/export.json")
def admin_levels_json(token: str = Query(None)): # Query 파라미터로 토큰 받기
    if not verify_token(token):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid admin token")
        
    js = json.dumps(groups_by_level(), ensure_ascii=False)
    return StreamingResponse(iter([js]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="levels.json"'}
    )
# =============================================

@app.post("/admin/levels/config")
def admin_levels_cfg(cfg: dict, background_tasks: BackgroundTasks, _: None = Depends(require_admin)):
    hi = float(cfg.get("high", LEVEL_CFG["high"]))
    lo = float(cfg.get("low", LEVEL_CFG["low"]))
    if not (0 <= lo < hi <= 100):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="invalid thresholds")
    LEVEL_CFG["high"], LEVEL_CFG["low"] = hi, lo
    
    background_tasks.add_task(broadcast_admin, {"type":"groups","data": groups_by_level(hi, lo)})
    
    return {"ok": True, "config": LEVEL_CFG}

# (이하 /calibration, /aoi, /export(svg/png) 엔드포인트들은 변경 사항 없음)

from math import sqrt
try:
    from PIL import Image, ImageDraw
except Exception as e:
    Image = None
    ImageDraw = None

CALIB: Dict[str, Dict] = {}
AOI_CFG: Dict[str, Dict] = {}

def apply_affine(x: float, y: float, coef: Dict[str, List[float]]) -> Tuple[float,float]:
    ax = coef["ax"]; ay = coef["ay"]
    x2 = ax[0]*x + ax[1]*y + ax[2]
    y2 = ay[0]*x + ay[1]*y + ay[2]
    return x2, y2

def fit_affine(src_xy: List[Tuple[float,float]], dst_xy: List[Tuple[float,float]]):
    if not np: return [1,0,0],[0,1,0]
    A = []
    bx = []
    by = []
    for (x,y),(u,v) in zip(src_xy, dst_xy):
        A.append([x,y,1.0])
        bx.append(u); by.append(v)
    A = np.array(A, dtype=float)
    bx = np.array(bx, dtype=float); by = np.array(by, dtype=float)
    ax, *_ = np.linalg.lstsq(A, bx, rcond=None)
    ay, *_ = np.linalg.lstsq(A, by, rcond=None)
    return ax.tolist(), ay.tolist()

def rmse(a: List[Tuple[float,float]], b: List[Tuple[float,float]]):
    s = 0.0
    for (x1,y1),(x2,y2) in zip(a,b):
        dx = x1-x2; dy = y1-y2
        s += dx*dx + dy*dy
    return (s/ max(1,len(a))) ** 0.5

@app.post("/calibration/{user_id}/start")
def calib_start(user_id: str, cfg: dict):
    n = int(cfg.get("n_points", 5))
    n = max(5, min(9, n))
    model = (cfg.get("model") or "affine").lower()
    CALIB[user_id] = {"samples": [], "n_points": n, "model": model}
    return {"ok": True, "n_points": n, "model": model}

@app.post("/calibration/{user_id}/sample")
def calib_sample(user_id: str, payload: dict):
    c = CALIB.setdefault(user_id, {"samples": []})
    c["samples"].append({"target": payload["target"], "obs": payload["obs"], "t": int(payload.get("t", time.time()*1000))})
    return {"ok": True, "count": len(c["samples"])}

@app.post("/calibration/{user_id}/finish")
def calib_finish(user_id: str):
    c = CALIB.get(user_id)
    if not c or not c.get("samples"):
        return JSONResponse({"error":"no samples"}, status_code=400)
    src = [(s["obs"][0], s["obs"][1]) for s in c["samples"]]
    dst = [(s["target"][0], s["target"][1]) for s in c["samples"]]
    ax, ay = fit_affine(src, dst)
    pred = [apply_affine(x,y, {"ax":ax,"ay":ay}) for x,y in src]
    e = rmse(pred, dst)
    CALIB[user_id].update({"model":"affine", "coef":{"ax":ax,"ay":ay}, "rmse": e, "n": len(src)})
    return {"ok": True, "model":"affine", "coef":{"ax":ax,"ay":ay}, "rmse": round(e,2), "n": len(src)}

@app.get("/calibration/{user_id}")
def calib_status(user_id: str):
    return CALIB.get(user_id, {"status":"none"})

def point_in_poly(x: float, y: float, poly: List[List[float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i+1)%n]
        if ((y1>y) != (y2>y)):
            xints = (x2-x1)*(y - y1) / (y2 - y1 + 1e-12) + x1
            if x < xints: inside = not inside
    return inside

@app.post("/aoi/{user_id}/set")
def aoi_set(user_id: str, cfg: dict):
    AOI_CFG[user_id] = {"aois": cfg.get("aois", [])}
    return {"ok": True, "count": len(AOI_CFG[user_id]["aois"])}

@app.get("/aoi/{user_id}/stats")
def aoi_stats(user_id: str):
    sess = SESSIONS.get(user_id)
    if not sess: return {"aois": [], "total_points": 0}
    aois = AOI_CFG.get(user_id, {}).get("aois", [])
    pts = sess.get("points", [])
    if not aois or not pts: return {"aois": [{"id":a["id"],"name":a.get("name",""),"entries":0,"dwell_ms":0} for a in aois], "total_points": len(pts)}
    res = {a["id"]: {"id":a["id"], "name": a.get("name",""), "entries":0, "dwell_ms":0} for a in aois}
    prev_hit = None
    for i,(t,x,y) in enumerate(pts):
        t2 = pts[i+1][0] if i+1 < len(pts) else t
        dt = max(0, t2 - t)
        hit_any = None
        for a in aois:
            if point_in_poly(x,y, a["poly"]):
                res[a["id"]]["dwell_ms"] += dt
                hit_any = a["id"]
        if hit_any != prev_hit and hit_any is not None:
            res[hit_any]["entries"] += 1
        prev_hit = hit_any
    return {"aois": list(res.values()), "total_points": len(pts)}

@app.get("/export/{user_id}.path.svg")
def export_path_svg(user_id: str, stroke: float = 2.0, color: str = "#2b8cff", w: Optional[int]=None, h: Optional[int]=None, margin: int=16, bg: Optional[str]=None):
    sess = SESSIONS.get(user_id)
    if not sess: return JSONResponse({"error":"no data"}, status_code=404)
    pts = sess.get("points", [])
    if not pts: return JSONResponse({"error":"empty"}, status_code=400)
    c = CALIB.get(user_id,{}) ; coef = c.get("coef")
    xy = []
    xs=[]; ys=[]
    for t,x,y in pts:
        if coef: x,y = apply_affine(x,y, coef)
        xs.append(x); ys.append(y); xy.append((x,y))
    if w is None: w = int((max(xs)-min(xs))+margin*2) or 100
    if h is None: h = int((max(ys)-min(ys))+margin*2) or 100
    minx, miny = min(xs), min(ys)
    def esc(s): return s.replace('"','&quot;') if isinstance(s,str) else s
    path_d = " ".join(f"L {x-minx+margin:.1f} {y-miny+margin:.1f}" for x,y in xy)
    if path_d.startswith("L"): path_d = "M" + path_d[1:]
    bg_rect = f'<rect x="0" y="0" width="{w}" height="{h}" fill="{esc(bg)}"/>' if bg else ""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
{bg_rect}<path d="{path_d}" fill="none" stroke="{esc(color)}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round"/></svg>'''
    return StreamingResponse(iter([svg]), media_type="image/svg+xml", headers={"Content-Disposition": f'attachment; filename="{user_id}.path.svg"'} )

def colormap_turbo(v: float):
    t = max(0.0, min(1.0, v))
    r = 34.61 + t*(1172.33 + t*(-10733.56 + t*(33300.12 + t*(-38394.49 + t*15064.50))))
    g = 23.31 + t*(557.33 + t*(1225.33 + t*(-3574.35 + t*(2630.13 + t*(-492.11)))))
    b = 27.20 + t*(321.09 + t*( -1525.90 + t*(4490.55 + t*(-4267.43 + t*1256.30))))
    return (int(max(0,min(255,r))), int(max(0,min(255,g))), int(max(0,min(255,b))))

def colormap_gray(v: float):
    g = int(max(0,min(255, round(v*255)))); return (g,g,g)

def normalize(values, mode="minmax"):
    if not np: return values
    mv = [v for v in values if v is not None]
    if not mv: return values
    if mode=="zscore":
        import statistics as st
        mu = st.mean(mv); sd = max(1e-6, st.pstdev(mv))
        return [(v-mu)/sd*0.2 + 0.5 for v in values]
    elif mode=="maxabs":
        m = max(abs(v) for v in mv) or 1.0
        return [v/m for v in values]
    else:
        lo = min(mv); hi = max(mv); span = hi-lo or 1.0
        return [(v-lo)/span for v in values]

@app.get("/export/{user_id}.heatmap.png")
def export_heatmap(user_id: str, w: Optional[int]=None, h: Optional[int]=None, radius: int=24, point_intensity: float=1.0, margin: int=16, colormap: str="turbo", gamma: float=1.0, norm: str="minmax", bg: Optional[str]=None):
    if Image is None:
        return JSONResponse({"error":"Pillow not installed"}, status_code=500)
    sess = SESSIONS.get(user_id)
    if not sess: return JSONResponse({"error":"no data"}, status_code=404)
    pts = sess.get("points", [])
    if not pts: return JSONResponse({"error":"empty"}, status_code=400)
    c = CALIB.get(user_id,{}) ; coef = c.get("coef")
    xs=[]; ys=[]; xy=[]
    for t,x,y in pts:
        if coef: x,y = apply_affine(x,y, coef)
        xs.append(x); ys.append(y); xy.append((x,y))
    if w is None: w = int((max(xs)-min(xs))+margin*2) or 100
    if h is None: h = int((max(ys)-min(ys))+margin*2) or 100
    minx, miny = min(xs), min(ys)

    import math
    W,H = w,h
    acc = [[0.0]*W for _ in range(H)]
    rad = max(3, int(radius))
    for (x,y) in xy:
        cx = int(round(x - minx + margin)); cy = int(round(y - miny + margin))
        if cx<0 or cy<0 or cx>=W or cy>=H: continue
        for yy in range(max(0, cy-rad), min(H, cy+rad+1)):
            dy = yy-cy
            for xx in range(max(0, cx-rad), min(W, cx+rad+1)):
                dx = xx-cx
                d2 = dx*dx + dy*dy
                if d2 <= rad*rad:
                    wgt = math.exp(-d2/(2*(rad*0.6)**2))
                    acc[yy][xx] += wgt * point_intensity

    flat = [v for row in acc for v in row]
    flat = normalize(flat, mode=norm)
    if abs(gamma-1.0) > 1e-3:
        flat = [max(0.0, min(1.0, v))**gamma for v in flat]

    cm = colormap.lower()
    def mapcolor(v):
        if cm=="gray": return colormap_gray(v)
        else: return colormap_turbo(v)
    img = Image.new("RGBA", (W,H), (0,0,0,0))
    px = img.load()
    k=0
    for yy in range(H):
        for xx in range(W): # <--- 여기를 range(W,H)에서 range(W)로 수정!
            if k < len(flat): # 인덱스 에러 방지용 안전장치 추가
                r,g,b = mapcolor(flat[k]); k+=1
                px[xx,yy] = (r,g,b, 220)
            
    if bg:
        bgimg = Image.new("RGB",(W,H), bg)
        bgimg.paste(img, (0,0), img)
        out = bgimg
    else:
        out = img
    bio = io.BytesIO(); out.save(bio, format="PNG")
    bio.seek(0)
    return StreamingResponse(bio, media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{user_id}.heatmap.png"'}
    )
