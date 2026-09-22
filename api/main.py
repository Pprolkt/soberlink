from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from db import get_connection
from token_service import create_session_payload, hash_token

app = FastAPI(title="SoberLink API")

# Local dev only: allows the dashboard page (opened as a file, different origin)
# to call this API from the browser. Tighten this before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DISCLAIMER = (
    "Risk indicator only. This is not a BAC estimate or automatic service decision. "
    "Final assessment remains with RSA-trained staff."
)

# How long a session stays alive since its last activity (checkin, drink,
# concern, or refusal). Change this ONE value to change it everywhere.
SESSION_HOURS = 12

# ---------- request bodies ----------

class CheckinRequest(BaseModel):
    venue_id: str

class DrinkRequest(BaseModel):
    venue_id: str
    drink_type: str
    volume_ml: float
    abv: float

class ConcernRequest(BaseModel):
    venue_id: str
    staff_id: str
    note: Optional[str] = ""

class RefusalRequest(BaseModel):
    venue_id: str
    staff_id: str
    note: Optional[str] = ""

# ---------- auth (token now comes from a header, not the URL) ----------

def get_token(authorization: str = Header(...)) -> str:
    """Expects: Authorization: Bearer <raw_token>
    Keeping the token in a header (instead of the URL) means it never shows up
    in Azure's request logs, unlike query params or path segments."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "invalid_authorization_header"})
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail={"error": "missing_token"})
    return token

# ---------- helpers ----------

def get_session_by_token(token: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT session_id, status, created_at, expires_at FROM sessions WHERE token_hash = %s;",
        (hash_token(token),),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "session_not_found"})
    session_id, status, created_at, expires_at = row
    if status == "active" and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail={"error": "session_expired"})
    return {"session_id": session_id, "status": status, "created_at": created_at, "expires_at": expires_at}

def get_venue_id(venue_code: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT venue_id FROM venues WHERE venue_code = %s;", (venue_code,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "venue_not_found"})
    return row[0]

def calculate_risk(session_id):
    """Option B: non-overlapping factors (3h volume, velocity flag, venues, refusal, staff concern)."""
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    # 3h volume
    cur.execute(
        "SELECT standard_drinks, recorded_at FROM drink_events WHERE session_id = %s ORDER BY recorded_at;",
        (session_id,),
    )
    drinks = cur.fetchall()
    drinks_3h = sum(float(d[0]) for d in drinks if d[1] >= now - timedelta(hours=3))

    # Drinking speed: SUPPORTING INFORMATION ONLY, per current spec — does not add
    # points to the score unless a separate scoring rule is defined later.
    # We surface the shortest gap between consecutive drinks so staff can see it.
    shortest_gap_minutes = None
    if len(drinks) >= 2:
        gaps = [
            (drinks[i][1] - drinks[i - 1][1]).total_seconds() / 60
            for i in range(1, len(drinks))
        ]
        shortest_gap_minutes = round(min(gaps), 1)

    # unique venues visited
    cur.execute("SELECT COUNT(DISTINCT venue_id) FROM venue_events WHERE session_id = %s;", (session_id,))
    unique_venues = cur.fetchone()[0]

    # previous refusal / staff concern this session
    cur.execute(
        "SELECT COUNT(*) FROM staff_events WHERE session_id = %s AND event_type = 'SERVICE_REFUSAL';",
        (session_id,),
    )
    previous_refusal = cur.fetchone()[0] > 0

    cur.execute(
        "SELECT COUNT(*) FROM staff_events WHERE session_id = %s AND event_type = 'STAFF_CONCERN';",
        (session_id,),
    )
    staff_concern = cur.fetchone()[0] > 0

    cur.close()
    conn.close()

    factors = []

    if drinks_3h >= 8:
        pts = 3
    elif drinks_3h >= 6:
        pts = 2
    elif drinks_3h >= 4:
        pts = 1
    else:
        pts = 0
    factors.append({"label": "3-hour volume", "points": pts})
    score = pts

    if unique_venues >= 3:
        pts = 2
    elif unique_venues == 2:
        pts = 1
    else:
        pts = 0
    factors.append({"label": "Venues visited", "points": pts})
    score += pts

    pts = 4 if previous_refusal else 0
    factors.append({"label": "Previous refusal", "points": pts})
    score += pts

    pts = 3 if staff_concern else 0
    factors.append({"label": "Staff concern", "points": pts})
    score += pts

    # time decay: no alcohol recorded for at least 2 hours
    if drinks and drinks[-1][1] < now - timedelta(hours=2):
        score = max(0, score - 1)
        factors.append({"label": "Time decay", "points": -1})
    else:
        factors.append({"label": "Time decay", "points": 0})

    score = max(0, score)

    if score >= 9:
        level = "VERY HIGH"
    elif score >= 6:
        level = "HIGH"
    elif score >= 3:
        level = "MODERATE"
    else:
        level = "LOW"

    return {
        "risk_score": score,
        "risk_level": level,
        "factors": factors,
        "shortest_gap_minutes": shortest_gap_minutes,
        "standard_drinks_3h": round(drinks_3h, 2),
        "unique_venues_visited": unique_venues,
        "previous_refusal": previous_refusal,
        "staff_concern": staff_concern,
    }

def standard_drinks(volume_ml: float, abv: float) -> float:
    # Australian standard: 10g pure alcohol. Ethanol density ~0.789 g/mL.
    return round((volume_ml * (abv / 100) * 0.789) / 10, 2)

# ---------- endpoints ----------

@app.get("/venues/{venue_code}/active-sessions")
def get_active_sessions_for_venue(venue_code: str, hours: float = 4):
    """Sessions that checked in at this venue within the last `hours` hours,
    with each one's current risk. Powers the venue's live screen."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT venue_id FROM venues WHERE venue_code = %s;", (venue_code,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "venue_not_found"})
    venue_id = row[0]

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cur.execute(
        """SELECT DISTINCT s.session_id
           FROM venue_events ve
           JOIN sessions s ON s.session_id = ve.session_id
           WHERE ve.venue_id = %s AND ve.event_type = 'CHECK_IN'
             AND ve.recorded_at >= %s AND s.status = 'active'
           ORDER BY s.session_id;""",
        (venue_id, cutoff),
    )
    session_ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    results = []
    for sid in session_ids:
        risk = calculate_risk(sid)
        results.append({"session_id": sid, **risk})
    return {"venue_code": venue_code, "active_sessions": results, "disclaimer": DISCLAIMER}

@app.post("/sessions", status_code=201)
def create_session_endpoint():
    # No token needed here — this is the one call that CREATES a token.
    payload = create_session_payload(session_hours=SESSION_HOURS)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (token_hash, expires_at) VALUES (%s, %s) RETURNING session_id;",
        (payload["token_hash"], payload["expires_at"]),
    )
    session_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {
        "session_id": session_id,
        "raw_token": payload["raw_token"],
        "expires_at": payload["expires_at"],
    }

@app.get("/sessions/me")
def get_session_endpoint(token: str = Depends(get_token)):
    s = get_session_by_token(token)
    return {
        "session_id": s["session_id"],
        "status": s["status"],
        "created_at": s["created_at"],
        "expires_at": s["expires_at"],
    }

@app.post("/sessions/checkin", status_code=201)
def checkin_endpoint(body: CheckinRequest, token: str = Depends(get_token)):
    s = get_session_by_token(token)
    venue_id = get_venue_id(body.venue_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO venue_events (session_id, venue_id, event_type) VALUES (%s, %s, 'CHECK_IN') RETURNING event_id, recorded_at;",
        (s["session_id"], venue_id),
    )
    event_id, recorded_at = cur.fetchone()
    cur.execute(
        "UPDATE sessions SET expires_at = now() + (%s * interval '1 hour') WHERE session_id = %s;",
        (SESSION_HOURS, s["session_id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"event_id": event_id, "event_type": "CHECK_IN", "recorded_at": recorded_at}

@app.post("/sessions/drinks", status_code=201)
def add_drink_endpoint(body: DrinkRequest, token: str = Depends(get_token)):
    s = get_session_by_token(token)
    venue_id = get_venue_id(body.venue_id)
    sd = standard_drinks(body.volume_ml, body.abv)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO drink_events (session_id, venue_id, drink_type, volume_ml, abv, standard_drinks)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING drink_event_id, recorded_at;""",
        (s["session_id"], venue_id, body.drink_type, body.volume_ml, body.abv, sd),
    )
    drink_event_id, recorded_at = cur.fetchone()
    cur.execute(
        "INSERT INTO venue_events (session_id, venue_id, event_type) VALUES (%s, %s, 'DRINK_RECORDED');",
        (s["session_id"], venue_id),
    )
    cur.execute(
        "UPDATE sessions SET expires_at = now() + (%s * interval '1 hour') WHERE session_id = %s;",
        (SESSION_HOURS, s["session_id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"drink_event_id": drink_event_id, "standard_drinks": sd, "recorded_at": recorded_at}

@app.post("/sessions/concerns", status_code=201)
def add_concern_endpoint(body: ConcernRequest, token: str = Depends(get_token)):
    s = get_session_by_token(token)
    venue_id = get_venue_id(body.venue_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO staff_events (session_id, staff_id, venue_id, event_type, note)
           VALUES (%s, %s, %s, 'STAFF_CONCERN', %s) RETURNING staff_event_id, recorded_at;""",
        (s["session_id"], body.staff_id, venue_id, body.note),
    )
    staff_event_id, recorded_at = cur.fetchone()
    cur.execute(
        "INSERT INTO venue_events (session_id, venue_id, event_type) VALUES (%s, %s, 'STAFF_CONCERN');",
        (s["session_id"], venue_id),
    )
    cur.execute(
        "UPDATE sessions SET expires_at = now() + (%s * interval '1 hour') WHERE session_id = %s;",
        (SESSION_HOURS, s["session_id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"staff_event_id": staff_event_id, "event_type": "STAFF_CONCERN", "recorded_at": recorded_at}

@app.post("/sessions/refusals", status_code=201)
def add_refusal_endpoint(body: RefusalRequest, token: str = Depends(get_token)):
    s = get_session_by_token(token)
    venue_id = get_venue_id(body.venue_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO staff_events (session_id, staff_id, venue_id, event_type, note)
           VALUES (%s, %s, %s, 'SERVICE_REFUSAL', %s) RETURNING staff_event_id, recorded_at;""",
        (s["session_id"], body.staff_id, venue_id, body.note),
    )
    staff_event_id, recorded_at = cur.fetchone()
    cur.execute(
        "INSERT INTO venue_events (session_id, venue_id, event_type) VALUES (%s, %s, 'SERVICE_REFUSAL');",
        (s["session_id"], venue_id),
    )
    cur.execute(
        "UPDATE sessions SET expires_at = now() + (%s * interval '1 hour') WHERE session_id = %s;",
        (SESSION_HOURS, s["session_id"]),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"staff_event_id": staff_event_id, "event_type": "SERVICE_REFUSAL", "recorded_at": recorded_at}

@app.get("/sessions/risk")
def get_risk_endpoint(token: str = Depends(get_token)):
    s = get_session_by_token(token)
    risk = calculate_risk(s["session_id"])
    return {"session_id": s["session_id"], **risk, "disclaimer": DISCLAIMER}

@app.delete("/sessions")
def end_session_endpoint(token: str = Depends(get_token)):
    s = get_session_by_token(token)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE sessions SET status = 'checked_out' WHERE session_id = %s;", (s["session_id"],))
    conn.commit()
    cur.close()
    conn.close()
    return {"session_id": s["session_id"], "status": "checked_out"}
