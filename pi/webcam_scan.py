import cv2
import hashlib
import sys
import os
import webbrowser
import socket

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'api'))
from db import get_connection

DASHBOARD_PORT = 8080

def get_local_ip():
    """Best-effort guess at this machine's LAN IP, for building the dashboard URL."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

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
            token = data.rsplit('/', 1)[-1]
            token_hash = hash_token(token)

            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT session_id, status FROM sessions WHERE token_hash = %s;", (token_hash,))
            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                print("MATCH FOUND - session_id:", row[0], "status:", row[1])
                ip = get_local_ip()
                dashboard_url = f"http://{ip}:{DASHBOARD_PORT}/dashboard.html?token={token}"
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
