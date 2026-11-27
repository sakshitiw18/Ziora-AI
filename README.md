# 🩺 Ziora AI: Voice Doctor Appointment Assistant

A real-time voice agent that answers a call in the browser and can **check a doctor's availability, book an appointment, and cancel one**, all in a single natural conversation. A dashboard next to the call shows the live transcript, the doctors and their slots, and the appointments, which update the moment the agent books or cancels.

The agent identifies the caller **only by the phone number they speak**. It reads the number back digit by digit and waits for a "yes" before it books or cancels. There is no OTP and no caller ID.

> 🧠 **LLM used: Google Gemini 3.5 Flash (`gemini-3.5-flash`)**
> 🗣️ **STT and TTS: ElevenLabs** · 🎛️ **Pipeline: Pipecat (Python)** · 🎚️ **VAD: Silero**

<!-- Add a screenshot or GIF here:  ![Ziora AI dashboard](docs/dashboard.png) -->

## ✨ Features

- 🎙️ **Voice pipeline (Pipecat):** mic audio, then Silero VAD, ElevenLabs STT, Gemini, ElevenLabs TTS, then audio back to the browser, with barge-in (interrupting the agent stops its speech).
- 🛠️ **Three LLM tools** registered in the pipeline via function calling: `check_doctor_availability`, `book_appointment`, `cancel_appointment`.
- 📞 **Phone-number identification with a code-level guard:** booking and cancelling are refused unless `phone_confirmed` is true, so the read-back rule is enforced in code and not just in the prompt.
- 🚫 **No double booking:** a unique SQLite index on `(doctor, date, slot)` for active bookings makes two callers unable to take the same slot.
- 📊 **Dashboard:** 10 mock doctors and their slots, a stats row, and an appointments list that highlights the row that was just booked or cancelled.
- 📱 **Phone-style call screen:** ring and connect states, call timer, mute and end buttons, a live waveform that reacts to both voices, a chat-style live transcript and an in-call booking confirmation card.
- ⚡ **Live updates:** the backend pushes `booked` and `cancelled` events over a WebSocket, so no refresh is needed.
- 🔐 **Accounts:** email/password sign-up and sign-in, so each person sees their own appointments (see [Limitations](#limitations)).

## 🏗️ Architecture

```
Browser (frontend/index.html)                  FastAPI + Pipecat (backend/)
  mic -> AudioWorklet -> PCM16 ---/ws/call--->   Silero VAD -> ElevenLabs STT -> Gemini <-> tools -> SQLite
  speaker <- PCM16 <-------------/ws/call----    <- ElevenLabs TTS <- Gemini
  live transcript <- JSON ------/ws/call----     transcript forwarders
  dashboard <- JSON events -----/ws/events--     tools broadcast "booked" / "cancelled"
  doctors / appointments / auth -- REST /api/*
```

Audio goes over the WebSocket as raw PCM16, mono, 16 kHz in both directions. A small custom serializer (`serializer.py`) translates between that and Pipecat frames, which keeps the frontend a single plain HTML file with no build step.

## 📁 Project structure

```
doctor-voice-ai/
├── frontend/
│   └── index.html          # Entire UI (HTML/CSS/JS)
├── backend/
│   ├── main.py             # FastAPI app: REST, auth, WebSockets, serves the frontend
│   ├── pipeline.py         # Pipecat pipeline, system prompt, transcript forwarding
│   ├── tools.py            # The 3 tools: schemas and handlers
│   ├── data.py             # SQLite mock data, booking rules, users
│   ├── serializer.py       # Browser <-> Pipecat audio/JSON wire format
│   ├── events.py           # Pushes live updates to the dashboard
│   ├── requirements.txt
│   └── .env.example
├── pipeline_description.md
└── README.md
```

## 🚀 Getting started

**Requirements:** Python 3.10+, Chrome (or another Chromium browser) with a microphone, and two free API keys:

- ElevenLabs: https://elevenlabs.io (used for both STT and TTS)
- Google Gemini: https://aistudio.google.com/apikey

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>/backend

python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\activate

pip install -r requirements.txt

# create your env file, then fill in the keys
cp .env.example .env          # Windows: copy .env.example .env

uvicorn main:app --port 8000
```

Wait for `Application startup complete`, then open **http://localhost:8000** (do not open the HTML file directly, or the browser will block the microphone). Sign up, click **Try Call**, allow the mic, and **use headphones** 🎧 to avoid echo.

The SQLite database (`backend/clinic.db`) is created automatically and the 10 doctors are seeded on every start. Delete the file to reset all appointments and users.

### 🔑 Environment variables (`backend/.env`)

| Variable | Required | Description |
|---|---|---|
| `ELEVENLABS_API_KEY` | yes | ElevenLabs key for STT and TTS |
| `ELEVENLABS_VOICE_ID` | no | TTS voice (defaults to a built-in voice) |
| `GOOGLE_API_KEY` | yes | Gemini API key |
| `GEMINI_MODEL` | no | Defaults to `gemini-3.5-flash` |
| `GOOGLE_CLIENT_ID` | no | Only needed if you wire up real Google sign-in |

⚠️ Never commit `.env`. It is already in `.gitignore`.

## 🎬 Try it

1. "Hi, I'd like to see a cardiologist."
2. The agent asks for your number: "My number is 9 8 7 6 5 4 3 2 1 0." Then confirm the read-back with "Yes."
3. The agent offers Dr. Vikram Mehta's free slots. Pick one and the agent books it.
4. The dashboard row lights up and the confirmation card appears in the call.
5. Say "Cancel my appointment", confirm your number, and watch the row change to *Cancelled*.

## 🧰 The tools

| Tool | What it does |
|---|---|
| `check_doctor_availability(doctor_name?, speciality?, date?)` | Returns each matching doctor's free slots for a date (default: tomorrow). |
| `book_appointment(phone_number, phone_confirmed, doctor_id, date, time)` | Books a slot. Fails if the number was not confirmed, the slot is taken, or the date is invalid. |
| `cancel_appointment(phone_number, phone_confirmed, appointment_id?)` | Cancels the caller's appointment, found by phone number. If there are several, the agent asks which one. |

## 🔌 API

| Endpoint | Purpose |
|---|---|
| `GET /api/doctors` | Doctors, their slot times, and the date shown on the dashboard |
| `GET /api/appointments?user_id=` | Appointments for an account |
| `POST /api/appointments/{id}/cancel` | Cancel from the dashboard |
| `POST /api/auth/signup`, `/signin`, `/google` | Account endpoints |
| `WS /ws/call` | Voice call: binary PCM audio plus JSON transcript messages |
| `WS /ws/events` | Live booked/cancelled events |

## ⚠️ Limitations

This is a demo project, so some parts are simplified on purpose:

- **Google sign-in is simulated.** The "Continue with Google" button sends an email and name to the backend; it does not verify a real Google OAuth token.
- **Password hashing is basic** (SHA-256 with a fixed salt). Use bcrypt or argon2 for anything real.
- **Doctors, slots and appointments are mock data** in a local SQLite file.
- Accounts only decide which appointments the dashboard shows. **The voice agent still identifies the caller only by the phone number they speak.**
- Dates are handled in the server's local time, and bookings are open for the next 14 days.
- Phone numbers are assumed to be 10-digit Indian mobile numbers (+91).

## 🛟 Troubleshooting

- 📭 **Dashboard is empty or shows "Backend not reachable":** make sure the server is running and you opened `http://localhost:8000`. Press F12 and check the Console for errors.
- 🤐 **Agent is silent for the first seconds:** the speech models load at startup. Wait for `Application startup complete` before clicking Try Call.
- 🔇 **No transcript or no reply:** check both API keys in `.env`, the terminal log for errors, and the browser's microphone permission.
- 🎧 **Choppy or repeating voice:** use headphones, and keep only one browser tab open.
- 🐛 **Import errors on startup:** Pipecat changes quickly. Run `pip show pipecat-ai`, check the docs for that version, and adjust the imports at the top of `pipeline.py` and `serializer.py`.

## 🧱 Tech stack

Python · FastAPI · Pipecat · ElevenLabs (STT and TTS) · Google Gemini · Silero VAD · SQLite · plain HTML/CSS/JS with the Web Audio API
