# DirectUpload Signage 📺

Lightweight, offline-first digital signage for **Raspberry Pi 5**.  
Upload an MP4 from any device → it plays full-screen on a TV via HDMI.

## Architecture

```
┌──────────────┐    Cloudflare     ┌─────────────────────────┐
│  Your Phone  │ ──── Tunnel ────→ │  Raspberry Pi 5         │
│  or Laptop   │   (trycloudflare) │                         │
│  /admin      │                   │  Flask :5000            │
└──────────────┘                   │   ├─ /admin (upload)    │
                                   │   └─ /      (player)   │
                                   │                         │
                                   │  Chromium (kiosk) ──→ TV│
                                   └─────────────────────────┘
```

## Screenshots

| Admin Panel | Login | Player |
|:-----------:|:-----:|:------:|
| ![Admin](screenshots/admin.png) | ![Login](screenshots/login.png) | ![Player](screenshots/player.png) |

## Prerequisites

```bash
# On your Raspberry Pi 5 (Bookworm)
sudo apt update && sudo apt install -y chromium-browser python3-pip

# Install cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64 \
  -o /usr/local/bin/cloudflared && sudo chmod +x /usr/local/bin/cloudflared

# Install Flask
pip3 install flask --break-system-packages
```

## Quick Start

```bash
chmod +x start.sh
./start.sh
```

The script will:
1. Start a Cloudflare Quick Tunnel (free, no account needed)
2. Save the public URL to `tunnel_url.txt`
3. Start the Flask server on port 5000
4. Launch Chromium in kiosk mode showing the player

## Usage

1. **Look at the TV** — the splash screen shows the admin URL for 20 seconds
2. **Open the admin URL** on your phone/laptop (`https://xxxx.trycloudflare.com/admin`)
3. **Upload an MP4** — it replaces the current video immediately
4. The player auto-detects changes and refreshes within 30 seconds

## Auto-Start on Boot (Optional)

Add to your desktop autostart:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/signage.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=DirectUpload Signage
Exec=/home/pi/directupload-signage/start.sh
X-GNOME-Autostart-enabled=true
EOF
```

## File Structure

```
directupload-signage/
├── app.py              # Flask server
├── start.sh            # Boot orchestrator
├── tunnel_url.txt      # Auto-generated tunnel URL
├── static/
│   └── video.mp4       # Current video (overwritten on upload)
└── templates/
    ├── admin.html       # Upload panel
    └── player.html      # Kiosk display
```
