# Ziora AI – Voice Doctor Appointment Assistant

A caller talks to a voice agent that can **check a doctor's availability, book, and cancel** appointments.
The caller is identified **only by the phone number they speak** (the agent reads it back before booking/cancelling).
A dashboard runs the call (phone-style UI), shows a live transcript, doctors/slots and appointments.

**LLM used: Google Gemini (`gemini-2.5-flash`, free quota).** STT + TTS: ElevenLabs. VAD: Silero.

## Architecture

```
Browser (index.html)                         FastAPI + Pipecat (backend/)
  mic -> AudioWorklet -> PCM16 --/ws/call-->  transport -> Silero VAD -> ElevenLabs STT
  speaker <- PCM16 <------------/ws/call---   <- ElevenLabs TTS <- Gemini <-> tools (SQLite)
  dashboard <- JSON events -----/ws/events--  tools broadcast "booked" / "cancelled"
  doctors / appointments ------ REST /api/*
```

## Run it

Requires Python 3.11+ and Chrome.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in ELEVENLABS_API_KEY and GOOGLE_API_KEY
uvicorn main:app --port 8000
```

Open **http://localhost:8000**, click **Try Call**, allow the mic, and use headphones (avoids echo).
The SQLite DB (`backend/clinic.db`) is created and seeded on first start. Delete it to reset.

## Try this (also the demo-video script)

1. "Hi, I'd like to see a cardiologist." – agent asks for your number
2. "My number is 9 8 7 6 5 4 3 2 1 0." – agent reads it back, say "yes"
3. Agent lists Dr. Vikram Mehta's slots – pick one – agent confirms and books
4. Watch the dashboard row highlight. Then: "Cancel my appointment."

## Project layout

| File | Role |
|---|---|
| `backend/main.py` | FastAPI app, REST + WebSockets, serves frontend |
| `backend/pipeline.py` | Pipecat pipeline, system prompt, transcript forwarding |
| `backend/tools.py` | `check_doctor_availability`, `book_appointment`, `cancel_appointment` |
| `backend/data.py` | SQLite mock data and booking rules |
| `backend/serializer.py` | Browser <-> Pipecat audio/JSON wire format |
| `backend/events.py` | Pushes live updates to the dashboard |

## Troubleshooting

- Import errors: Pipecat changes fast. Run `pip show pipecat-ai`, check the docs for that version, and adjust the imports at the top of `pipeline.py` / `serializer.py`.
- No transcript appears: confirm both API keys, and that Chrome has mic permission.