# Audio Chat (Flask)

Simple Flask app that accepts audio uploads from the browser and stores conversation messages persistently in a SQLite database.

Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

API endpoints
- GET /api/messages - list stored messages
- POST /api/messages - add a message (JSON: {role: 'user'|'bot', text: '...', audio_filename: null})
- POST /api/upload_audio - upload audio file (form field name `audio`, optional form field `transcript`).

Notes
- The bot integration is left to you; use the POST /api/messages endpoint to insert bot responses.
- Audio files are saved in `uploads/` and messages are persisted in `chat.db`.
