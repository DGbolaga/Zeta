from elevenlabs import ElevenLabs, save   # ✅ simplified import
from dotenv import load_dotenv
import os
import sqlite3
import uuid
import threading
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, g
import requests

# Load environment variables
load_dotenv()

# ✅ Initialize ElevenLabs client
client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "chat.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["DATABASE"] = DB_PATH

logging.basicConfig(level=logging.INFO)

WEBHOOK_URL = "https://idrak1ai.app.n8n.cloud/webhook/0292bfe4-98cd-4579-b5e3-219bb903e646"


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(app.config["DATABASE"]) 
        db.row_factory = sqlite3.Row
    return db


def init_db():
    with app.app_context():
        db = get_db()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                text TEXT,
                audio_filename TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        db.commit()


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    db = get_db()
    if request.method == "GET":
        cur = db.execute("SELECT * FROM messages ORDER BY id ASC")
        rows = cur.fetchall()
        msgs = []
        for r in rows:
            msgs.append({
                "id": r["id"],
                "role": r["role"],
                "text": r["text"],
                "audio_filename": r["audio_filename"],
                "created_at": r["created_at"],
            })
        return jsonify(msgs)

    data = request.get_json(force=True)
    role = data.get("role")
    text = data.get("text")
    audio_filename = data.get("audio_filename")
    if role not in ("user", "bot"):
        return jsonify({"error": "role must be 'user' or 'bot'"}), 400
    now = datetime.utcnow().isoformat()
    cur = db.execute(
        "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
        (role, text, audio_filename, now),
    )
    db.commit()
    msg_id = cur.lastrowid
    return jsonify({"id": msg_id, "role": role, "text": text, "audio_filename": audio_filename, "created_at": now})


@app.route("/api/upload_audio", methods=["POST"])
def upload_audio():
    if "audio" not in request.files:
        return jsonify({"error": "no audio file provided"}), 400
    f = request.files["audio"]
    orig_name = f.filename or "audio"
    ext = os.path.splitext(orig_name)[1] or ".webm"
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    f.save(save_path)

    transcript = request.form.get("transcript")
    now = datetime.utcnow().isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
        ("user", transcript, filename, now),
    )
    db.commit()

    def background_send(file_path, orig_filename, transcript_text):
        try:
            with open(file_path, "rb") as file_obj:
                files = {"audio": (orig_filename, file_obj, "application/octet-stream")}
                data = {"transcript": transcript_text or ""}
                resp = requests.post(WEBHOOK_URL, files=files, data=data, timeout=60)
                logging.info(f"Webhook POST status: {resp.status_code} / {resp.text}")
                
                if resp.status_code == 200:
                    try:
                        response_data = resp.json()
                        bot_text = response_data.get("text") or response_data.get("response") or response_data.get("message")
                        bot_audio = response_data.get("audio_filename")

                        # ✅ Generate TTS if text exists and no audio provided
                        if bot_text and not bot_audio:
                            try:
                                speech = client.text_to_speech.convert(
                                    text=bot_text,
                                    voice_id="EXAVITQu4vr4xnSDxMaL",
                                    model_id="eleven_multilingual_v2"
                                )
                                bot_audio = f"{uuid.uuid4().hex}.mp4"
                                audio_path = os.path.join(app.config["UPLOAD_FOLDER"], bot_audio)
                                save(speech, audio_path)
                            except Exception as e:
                                logging.error(f"Failed ElevenLabs TTS: {e}")
                                bot_audio = None

                        if bot_text or bot_audio:
                            now = datetime.utcnow().isoformat()
                            db_conn = sqlite3.connect(app.config["DATABASE"])
                            db_conn.row_factory = sqlite3.Row
                            db_conn.execute(
                                "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
                                ("bot", bot_text, bot_audio, now),
                            )
                            db_conn.commit()
                            db_conn.close()
                    except ValueError:
                        if resp.text.strip():
                            now = datetime.utcnow().isoformat()
                            db_conn = sqlite3.connect(app.config["DATABASE"])
                            db_conn.row_factory = sqlite3.Row
                            bot_text = resp.text.strip()

                            # ✅ Generate TTS for plain text response
                            try:
                                speech = client.text_to_speech.convert(
                                    text=bot_text,
                                    voice_id="EXAVITQu4vr4xnSDxMaL",
                                    model_id="eleven_multilingual_v2"
                                )
                                bot_audio = f"{uuid.uuid4().hex}.mp3"
                                audio_path = os.path.join(app.config["UPLOAD_FOLDER"], bot_audio)
                                save(speech, audio_path)
                            except Exception as e:
                                logging.error(f"Failed ElevenLabs TTS: {e}")
                                bot_audio = None

                            db_conn.execute(
                                "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
                                ("bot", bot_text, bot_audio, now),
                            )
                            db_conn.commit()
                            db_conn.close()
                else:
                    logging.error(f"Webhook failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logging.exception("Failed to POST audio to webhook")

    t = threading.Thread(target=background_send, args=(save_path, filename, transcript), daemon=True)
    t.start()
    return jsonify({"success": True, "filename": filename, "transcript": transcript})


@app.route("/api/messages/<int:msg_id>", methods=["DELETE"])
def delete_message(msg_id):
    db = get_db()
    cur = db.execute("SELECT audio_filename FROM messages WHERE id = ?", (msg_id,))
    row = cur.fetchone()
    if row is None:
        return jsonify({"error": "message not found"}), 404

    audio_filename = row["audio_filename"]
    if audio_filename:
        try:
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], audio_filename)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            logging.exception("Failed to remove audio file for message %s", msg_id)

    db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/webhook_receive", methods=["POST"])
def webhook_receive():
    try:
        data = request.get_json(force=True)
        role = data.get("role", "bot")
        text = data.get("text")
        audio_filename = data.get("audio_filename")

        # ✅ Generate TTS if only text is provided
        if text and not audio_filename:
            try:
                speech = client.text_to_speech.convert(
                    text=text,
                    voice_id="EXAVITQu4vr4xnSDxMaL",
                    model_id="eleven_multilingual_v2"
                )
                audio_filename = f"{uuid.uuid4().hex}.mp3"
                audio_path = os.path.join(app.config["UPLOAD_FOLDER"], audio_filename)
                save(speech, audio_path)
            except Exception as e:
                logging.error(f"Failed ElevenLabs TTS: {e}")
                audio_filename = None

        if not text and not audio_filename:
            return jsonify({"error": "must include 'text' or 'audio_filename'"}), 400

        now = datetime.utcnow().isoformat()
        db = get_db()
        cur = db.execute(
            "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
            (role, text, audio_filename, now),
        )
        db.commit()
        msg_id = cur.lastrowid

        return jsonify({
            "success": True,
            "id": msg_id,
            "role": role,
            "text": text,
            "audio_filename": audio_filename,
            "created_at": now
        })
    except Exception as e:
        logging.exception("Failed to handle incoming webhook data")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)














# from elevenlabs import ElevenLabs
# from elevenlabs import play, save
# from dotenv import load_dotenv
# import os
# import sqlite3
# import uuid
# import threading
# import logging
# from datetime import datetime
# from flask import Flask, render_template, request, jsonify, send_from_directory, g

# # We'll use requests to POST the uploaded audio to the external webhook
# import requests

# # Load environment variables
# load_dotenv()




# BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# DB_PATH = os.path.join(BASE_DIR, "chat.db")
# UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
# os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# app = Flask(__name__)
# app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
# app.config["DATABASE"] = DB_PATH

# # Configure simple logging so you can see background-send results in the console
# logging.basicConfig(level=logging.INFO)

# # The webhook URL you asked to forward audio to. You can change this later.
# WEBHOOK_URL = "https://ztesting.app.n8n.cloud/webhook-test/0292bfe4-98cd-4579-b5e3-219bb903e646"


# def get_db():
#     db = getattr(g, "_database", None)
#     if db is None:
#         db = g._database = sqlite3.connect(app.config["DATABASE"]) 
#         db.row_factory = sqlite3.Row
#     return db


# def init_db():
#     with app.app_context():
#         db = get_db()
#         db.execute(
#             """
#             CREATE TABLE IF NOT EXISTS messages (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 role TEXT NOT NULL,
#                 text TEXT,
#                 audio_filename TEXT,
#                 created_at TEXT NOT NULL
#             )
#             """
#         )
#         db.commit()


# @app.teardown_appcontext
# def close_connection(exception):
#     db = getattr(g, "_database", None)
#     if db is not None:
#         db.close()


# @app.route("/")
# def index():
#     return render_template("index.html")


# @app.route("/uploads/<path:filename>")
# def uploaded_file(filename):
#     return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# @app.route("/api/messages", methods=["GET", "POST"])
# def messages():
#     db = get_db()
#     if request.method == "GET":
#         cur = db.execute("SELECT * FROM messages ORDER BY id ASC")
#         rows = cur.fetchall()
#         msgs = []
#         for r in rows:
#             msgs.append({
#                 "id": r["id"],
#                 "role": r["role"],
#                 "text": r["text"],
#                 "audio_filename": r["audio_filename"],
#                 "created_at": r["created_at"],
#             })
#         return jsonify(msgs)

#     # POST: add a generic message (useful for bot or manual adds)
#     data = request.get_json(force=True)
#     role = data.get("role")
#     text = data.get("text")
#     audio_filename = data.get("audio_filename")
#     if role not in ("user", "bot"):
#         return jsonify({"error": "role must be 'user' or 'bot'"}), 400
#     now = datetime.utcnow().isoformat()
#     cur = db.execute(
#         "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
#         (role, text, audio_filename, now),
#     )
#     db.commit()
#     msg_id = cur.lastrowid
#     return jsonify({"id": msg_id, "role": role, "text": text, "audio_filename": audio_filename, "created_at": now})


# @app.route("/api/upload_audio", methods=["POST"])
# def upload_audio():
#     # Expects 'audio' file field and optional 'transcript' form field.
#     # Flow:
#     # 1. Save the uploaded file to disk (in `uploads/`).
#     # 2. Persist a message row in SQLite for persistence.
#     # 3. Send the audio file (and transcript if present) to the configured
#     #    external webhook in a background thread so the API call returns quickly.

#     if "audio" not in request.files:
#         return jsonify({"error": "no audio file provided"}), 400
#     f = request.files["audio"]
#     # keep original extension when possible; default to .webm
#     orig_name = f.filename or "audio"
#     ext = os.path.splitext(orig_name)[1] or ".webm"
#     filename = f"{uuid.uuid4().hex}{ext}"
#     save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

#     # Save to disk first (persistence requirement)
#     f.save(save_path)

#     # Optional transcript text sent from the client
#     transcript = request.form.get("transcript")

#     # Persist message to SQLite immediately
#     now = datetime.utcnow().isoformat()
#     db = get_db()
#     db.execute(
#         "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
#         ("user", transcript, filename, now),
#     )
#     db.commit()

#     # Background sender: posts the audio file and transcript to the external webhook.
#     # It runs in a separate thread to avoid blocking the request/response cycle.
    


#     def background_send(file_path, orig_filename, transcript_text):
#         try:
#             # Use a longer timeout to wait for the webhook processing
#             with open(file_path, "rb") as file_obj:
#                 files = {"audio": (orig_filename, file_obj, "application/octet-stream")}
#                 data = {"transcript": transcript_text or ""}
#                 # You can add headers or auth here if your webhook requires it
#                 resp = requests.post(WEBHOOK_URL, files=files, data=data, timeout=60)
#                 logging.info(f"Webhook POST status: {resp.status_code} / {resp.text}")
                
#                 # NEW: Handle the response from the webhook and display in terminal
#                 if resp.status_code == 200:
#                     print(f"🔄 Webhook Response: {resp.text}")
#                     logging.info(f"Webhook returned: {resp.text}")
                    
#                     # Try to parse as JSON first
#                     try:
#                         response_data = resp.json()
#                         print(f"📦 Webhook JSON Response: {response_data}")
                        
#                         # Extract response text or other relevant data
#                         bot_text = response_data.get("text") or response_data.get("response") or response_data.get("message")
#                         bot_audio = response_data.get("audio_filename")
                        
#                         if bot_text or bot_audio:
#                             # Store the bot response in the database
#                             now = datetime.utcnow().isoformat()
#                             # We need to get a new database connection since we're in a different thread
#                             db_conn = sqlite3.connect(app.config["DATABASE"])
#                             db_conn.row_factory = sqlite3.Row
                            
#                             db_conn.execute(
#                                 "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
#                                 ("bot", bot_text, bot_audio, now),
#                             )
#                             db_conn.commit()
#                             db_conn.close()
                            
#                             print(f"💾 Stored bot response: {bot_text}")
#                             logging.info(f"Stored bot response: {bot_text}")
                        
#                     except ValueError:
#                         # Response is not JSON, treat the entire response text as the bot message
#                         if resp.text.strip():
#                             now = datetime.utcnow().isoformat()
#                             db_conn = sqlite3.connect(app.config["DATABASE"])
#                             db_conn.row_factory = sqlite3.Row
                            
#                             db_conn.execute(
#                                 "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
#                                 ("bot", resp.text.strip(), None, now),
#                             )
#                             db_conn.commit()
#                             db_conn.close()
                            
#                             print(f"💾 Stored bot response (plain text): {resp.text.strip()}")
#                             logging.info(f"Stored bot response (plain text): {resp.text.strip()}")
#                 else:
#                     print(f"❌ Webhook failed with status: {resp.status_code}")
#                     logging.error(f"Webhook failed with status: {resp.status_code} - {resp.text}")
                            
#         except Exception as e:
#             # Log the error. We intentionally do not raise, since persistence has
#             # already happened locally — this keeps the user flow robust.
#             print(f"💥 Failed to POST audio to webhook: {e}")
#             logging.exception("Failed to POST audio to webhook")


#     # Start the background thread (daemon so it won't block process exit)
#     t = threading.Thread(target=background_send, args=(save_path, filename, transcript), daemon=True)
#     t.start()

#     # Return success to the client immediately (the webhook send is best-effort)
#     return jsonify({"success": True, "filename": filename, "transcript": transcript})


# @app.route("/api/messages/<int:msg_id>", methods=["DELETE"])
# def delete_message(msg_id):
#     """
#     Delete a message by id. This removes the database row and will also
#     attempt to delete the associated audio file from disk (if any).

#     Returns JSON {success: true} on success or an error with an appropriate
#     HTTP status code. We keep this simple (no auth) — add auth checks here
#     if you need to restrict who can delete messages.
#     """
#     db = get_db()
#     cur = db.execute("SELECT audio_filename FROM messages WHERE id = ?", (msg_id,))
#     row = cur.fetchone()
#     if row is None:
#         return jsonify({"error": "message not found"}), 404

#     audio_filename = row["audio_filename"]
#     # Remove the audio file from disk if it exists
#     if audio_filename:
#         try:
#             file_path = os.path.join(app.config["UPLOAD_FOLDER"], audio_filename)
#             if os.path.exists(file_path):
#                 os.remove(file_path)
#         except Exception:
#             logging.exception("Failed to remove audio file for message %s", msg_id)

#     # Delete the DB row
#     db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
#     db.commit()
#     return jsonify({"success": True})

# @app.route("/api/webhook_receive", methods=["POST"])
# def webhook_receive():
#     """
#     Endpoint to receive data from the external webhook.
#     This lets the webhook POST data back into this system.
#     """
#     try:
#         # 🔹 Log the raw HTTP headers and body
#         logging.info(f"Webhook headers: {dict(request.headers)}")
#         logging.info(f"Webhook raw body: {request.data.decode(errors='ignore')}")
#         print(f"📩 Headers: {dict(request.headers)}")
#         print(f"📩 Raw body: {request.data.decode(errors='ignore')}")

#         data = request.get_json(force=True)

#         # 🔹 Log + print the parsed JSON
#         logging.info(f"Received webhook data (parsed JSON): {data}")
#         print(f"✅ Parsed JSON: {data}")

#         # Example: Expecting role, text, maybe audio URL/path from webhook
#         role = data.get("role", "bot")  # default to "bot"
#         text = data.get("text")
#         audio_filename = data.get("audio_filename")

#         if not text and not audio_filename:
#             msg = "must include 'text' or 'audio_filename'"
#             logging.error(msg)
#             print(f"❌ Error: {msg}")
#             return jsonify({"error": msg}), 400

#         # Store in SQLite just like other messages
#         now = datetime.utcnow().isoformat()
#         db = get_db()
#         cur = db.execute(
#             "INSERT INTO messages (role, text, audio_filename, created_at) VALUES (?, ?, ?, ?)",
#             (role, text, audio_filename, now),
#         )
#         db.commit()
#         msg_id = cur.lastrowid

#         # 🔹 Log the saved message ID
#         logging.info(f"Webhook message stored with ID: {msg_id}")
#         print(f"💾 Stored webhook message ID: {msg_id}")

#         return jsonify({
#             "success": True,
#             "id": msg_id,
#             "role": role,
#             "text": text,
#             "audio_filename": audio_filename,
#             "created_at": now
#         })

#     except Exception as e:
#         logging.exception("Failed to handle incoming webhook data")
#         print(f"💥 Exception: {e}")
#         return jsonify({"error": str(e)}), 500




# if __name__ == "__main__":
#     init_db()
#     app.run(host="0.0.0.0", port=5000, debug=True)
