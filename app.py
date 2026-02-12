#!/usr/bin/env python3
"""
DirectUpload Signage — Lightweight digital signage server for Raspberry Pi 5.
"""

import os
import shutil
import uuid
import glob
import subprocess
from functools import wraps
# DÜZELTME 1: 'session' eklendi
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
VIDEO_PATH = os.path.join(STATIC_DIR, "video.mp4")
CHUNKS_DIR = os.path.join(BASE_DIR, ".chunks")
TUNNEL_URL_FILE = os.path.join(BASE_DIR, "tunnel_url.txt")

ALLOWED_EXTENSIONS = {"mp4", "MP4"}
# ŞİFRE BURADA:
ADMIN_PASSWORD = "bloom"

# Per-request limit: 60 MB
MAX_CHUNK_BYTES = 60 * 1024 * 1024
# Total video limit: 4 GB
MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["MAX_CONTENT_LENGTH"] = MAX_CHUNK_BYTES

upload_sessions = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1] in ALLOWED_EXTENSIONS


def _read_tunnel_url() -> str:
    try:
        with open(TUNNEL_URL_FILE, "r") as f:
            url = f.read().strip()
            return url if url else "Tunnel URL not available yet"
    except FileNotFoundError:
        return "Tunnel URL not available yet"


def _video_exists() -> bool:
    return os.path.isfile(VIDEO_PATH)


def _cleanup_chunks(session_id: str):
    """Remove chunk files for a session."""
    session_dir = os.path.join(CHUNKS_DIR, session_id)
    if os.path.isdir(session_dir):
        shutil.rmtree(session_dir, ignore_errors=True)
    upload_sessions.pop(session_id, None)

def get_cpu_temp():
    try:
        # Raspberry Pi sıcaklık sensörünü okur
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = int(f.read()) / 1000.0
        return f"{temp:.1f}°C"
    except:
        return "N/A"

def get_disk_usage():
    # Disk doluluk oranını okur
    total, used, free = shutil.disk_usage("/")
    free_gb = free // (2**30)
    total_gb = total // (2**30)
    return f"{free_gb} GB / {total_gb} GB"

# --- GÜVENLİK BEKÇİSİ ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "logged_in" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")
        if password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("admin"))
        else:
            flash("Yanlış şifre Patron! Tekrar dene.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Page Routes
# ---------------------------------------------------------------------------
@app.route("/")
def player():
    # Player sayfası herkese açık kalmalı (TV izleyecek çünkü)
    tunnel_url = _read_tunnel_url()
    has_video = _video_exists()
    return render_template("player.html", tunnel_url=tunnel_url, has_video=has_video)


@app.route("/admin")
@login_required
def admin():
    has_video = _video_exists()
    video_size_mb = (
        round(os.path.getsize(VIDEO_PATH) / (1024 * 1024), 1) if has_video else 0
    )
    tunnel_url = _read_tunnel_url()

    # YENİ EKLENENLER:
    cpu_temp = get_cpu_temp()
    disk_space = get_disk_usage()

    return render_template(
        "admin.html",
        has_video=has_video,
        video_size_mb=video_size_mb,
        tunnel_url=tunnel_url,
        max_video_gb=MAX_VIDEO_BYTES / (1024 ** 3),
        cpu_temp=cpu_temp,   # HTML'e gönderiyoruz
        disk_space=disk_space # HTML'e gönderiyoruz
    )


# ---------------------------------------------------------------------------
# Chunked Upload API (HEPSİ KİLİTLENDİ)
# ---------------------------------------------------------------------------
@app.route("/upload/init", methods=["POST"])
@login_required  # <--- KİLİT
def upload_init():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    total_chunks = data.get("totalChunks", 0)
    file_size = data.get("fileSize", 0)

    if not filename or not _allowed(filename):
        return jsonify({"error": "Only .mp4 files are accepted."}), 400

    if file_size > MAX_VIDEO_BYTES:
        return jsonify({"error": f"File too large. Max {MAX_VIDEO_BYTES // (1024**3)} GB."}), 400

    session_id = uuid.uuid4().hex[:12]
    session_dir = os.path.join(CHUNKS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    upload_sessions[session_id] = {
        "filename": filename,
        "total_chunks": total_chunks,
        "received": 0,
    }

    return jsonify({"sessionId": session_id}), 200


@app.route("/upload/chunk", methods=["POST"])
@login_required  # <--- KİLİT
def upload_chunk():
    session_id = request.form.get("sessionId", "")
    chunk_index = request.form.get("chunkIndex", "")

    if session_id not in upload_sessions:
        return jsonify({"error": "Invalid session."}), 400

    chunk_file = request.files.get("chunk")
    if not chunk_file:
        return jsonify({"error": "No chunk data."}), 400

    session_dir = os.path.join(CHUNKS_DIR, session_id)
    chunk_path = os.path.join(session_dir, f"{int(chunk_index):06d}.part")
    chunk_file.save(chunk_path)

    upload_sessions[session_id]["received"] += 1

    return jsonify({"received": upload_sessions[session_id]["received"]}), 200


@app.route("/upload/finish", methods=["POST"])
@login_required  # <--- KİLİT
def upload_finish():
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")

    if session_id not in upload_sessions:
        return jsonify({"error": "Invalid session."}), 400

    session_info = upload_sessions[session_id]
    session_dir = os.path.join(CHUNKS_DIR, session_id)

    parts = sorted(glob.glob(os.path.join(session_dir, "*.part")))

    if len(parts) != session_info["total_chunks"]:
        _cleanup_chunks(session_id)
        return jsonify({
            "error": f"Chunk mismatch: expected {session_info['total_chunks']}, got {len(parts)}."
        }), 400

    os.makedirs(STATIC_DIR, exist_ok=True)
    tmp_path = VIDEO_PATH + ".tmp"
    try:
        with open(tmp_path, "wb") as out:
            for part in parts:
                with open(part, "rb") as p:
                    shutil.copyfileobj(p, out, length=1024 * 1024)

        if os.path.exists(VIDEO_PATH):
            os.remove(VIDEO_PATH)
        os.rename(tmp_path, VIDEO_PATH)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        _cleanup_chunks(session_id)
        return jsonify({"error": f"Assembly failed: {str(e)}"}), 500

    _cleanup_chunks(session_id)
    final_size = os.path.getsize(VIDEO_PATH)

    return jsonify({
        "success": True,
        "size_mb": round(final_size / (1024 * 1024), 1),
    }), 200


@app.route("/upload/cancel", methods=["POST"])
@login_required  # <--- KİLİT
def upload_cancel():
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")
    _cleanup_chunks(session_id)
    return jsonify({"cancelled": True}), 200


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({"error": "Chunk too large. Max 50 MB per chunk."}), 413

@app.route("/reboot", methods=["POST"])
@login_required
def reboot_device():
    # Cihazı 1 saniye sonra yeniden başlat
    subprocess.Popen(["sudo", "reboot"])
    return jsonify({"success": True, "message": "Cihaz yeniden başlatılıyor..."})

# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(CHUNKS_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=False)