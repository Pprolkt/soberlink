import qrcode

# EDIT THIS to your laptop's actual LAN IP before generating (see instructions).
# Find it with `ipconfig` on Windows — look for "IPv4 Address" under your WiFi adapter.
LAPTOP_LAN_IP = "172.20.10.4"  # <-- your current hotspot IP
PORT = 8080  # the port you'll serve the dashboard folder on

VENUES = ["VEN-001", "VEN-002", "VEN-003"]

for venue in VENUES:
    url = f"http://{LAPTOP_LAN_IP}:{PORT}/checkin.html?venue={venue}"
    img = qrcode.make(url)
    filename = f"venue_qr_{venue}.png"
    img.save(filename)
    print(f"{venue}: {url}  ->  saved as {filename}")
    
