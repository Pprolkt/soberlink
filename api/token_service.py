import uuid
import hashlib
from datetime import datetime, timedelta, timezone

from db import get_connection


def generate_token() -> str:
    """Cryptographically secure random token (UUIDv4)."""
    return str(uuid.uuid4())


def hash_token(raw_token: str) -> str:
    """SHA-256 hash of the raw token. This is what's stored in the DB —
    the raw token itself never touches the database."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session_payload(session_hours: int = 8) -> dict:
    """Builds a new token + hash + expiry, without touching the database.
    Useful for testing the token/hash logic in isolation."""
    raw_token = generate_token()
    token_hash = hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=session_hours)
    return {
        "raw_token": raw_token,    # goes into the QR code, never stored
        "token_hash": token_hash,  # this is what gets inserted into sessions.token_hash
        "expires_at": expires_at,
    }


def create_session(session_hours: int = 8) -> dict:
    """Creates a session, inserts it into the DB, and returns the raw token
    (only returned here — never stored, never returned by any other call)."""
    payload = create_session_payload(session_hours)

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO sessions (token_hash, expires_at) VALUES (%s, %s) RETURNING session_id;",
            (payload["token_hash"], payload["expires_at"]),
        )
        session_id = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    payload["session_id"] = session_id
    return payload


if __name__ == "__main__":
    result = create_session()
    print("Session created:        ", result["session_id"])
    print("Raw token (for QR code):", result["raw_token"])
    print("Token hash (for DB):    ", result["token_hash"])
    print("Expires at:             ", result["expires_at"])
