import cv2
import hashlib
import sys
import os
import webbrowser
from urllib.parse import urlparse, parse_qs

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api'))
from db import get_connection

DASHBOARD_PORT = 8080
# EDIT THIS to match your laptop's actual LAN/hotspot IP — same value you use
# in generate_venue_qrs.py.
DASHBOARD_HOST = "172.20.10.4"

def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

def extract_token(url: str) -> str:
    """Pulls the token out of either URL style:
    - http://host:port/dashboard.html?token=XYZ  (current format)
    - http://host/session/XYZ                     (old format, just in case)
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "token" in qs and qs["token"]:
        return qs["token"][0]
    # fallback: last path segment (old-style URLs with no query string)
    return url.rstrip("/").rsplit("/", 1)[-1]

def scan_and_open_dashboard():
    detector = cv2.QRCodeDetector()
    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        print("ERROR: could not open webcam.")
        return

    print("Webcam opened. Point a patron's QR at it. Press 'q' to quit.")
    frame_count = 0

    while True:
        ok, frame = cam.read()
        if not ok:
            print("ERROR: failed to read frame.")
            break

        frame_count += 1
        if frame_count % 30 == 0:
            print("Still scanning... frame", frame_count)

        data, points, _ = detector.detectAndDecode(frame)
        if data:
            print("Decoded URL:", data)
            token = extract_token(data)
            print("Extracted token:", token)
            token_hash = hash_token(token)

            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT session_id, status FROM sessions WHERE token_hash = %s;", (token_hash,))
            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                print("MATCH FOUND - session_id:", row[0], "status:", row[1])
                dashboard_url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/dashboard.html?token={token}"
                print("Opening dashboard:", dashboard_url)
                webbrowser.open(dashboard_url)
            else:
                print("No matching session (expired or invalid).")
            break

        cv2.imshow("Scan QR", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("Quit by user.")
            break

    cam.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    scan_and_open_dashboard()
