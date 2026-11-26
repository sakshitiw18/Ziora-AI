import os
"""FastAPI server: REST for the dashboard, WebSockets for the call + live events, serves the frontend."""
import json
import base64
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before pipeline.py reads env vars

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from loguru import logger

import data
import events
from pipeline import run_pipeline

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Ziora AI - Voice Doctor Appointment Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
data.init_db()


# ---------- Auth Models & Endpoints ----------
class SignUpReq(BaseModel):
    email: str
    password: str
    name: str = ""


class SignInReq(BaseModel):
    email: str
    password: str


class GoogleAuthReq(BaseModel):
    email: str = ""
    name: str = ""
    picture: str | None = None
    credential: str | None = None
    google_id: str | None = None


@app.get("/api/auth/config")
def get_auth_config():
    return {
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID", "")
    }

@app.post("/api/auth/signup")
def signup(req: SignUpReq):
    try:
        user = data.create_user(req.email, req.password, req.name, provider="local")
        return user
    except data.DataError as e:
        raise HTTPException(400, str(e))


@app.post("/api/auth/signin")
def signin(req: SignInReq):
    try:
        user = data.authenticate_user(req.email, req.password)
        return user
    except data.DataError as e:
        raise HTTPException(401, str(e))


@app.post("/api/auth/google")
def google_auth(req: GoogleAuthReq):
    email = req.email
    name = req.name
    picture = req.picture
    google_id = req.google_id

    # If Google Credential JWT is provided (from GIS One Tap or Google Button)
    if req.credential:
        try:
            parts = req.credential.split(".")
            if len(parts) >= 2:
                payload_str = parts[1]
                payload_str += "=" * (-len(payload_str) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_str).decode("utf-8"))
                email = payload.get("email", email)
                name = payload.get("name") or payload.get("given_name", name)
                picture = payload.get("picture", picture)
                google_id = payload.get("sub", google_id)
        except Exception as e:
            logger.warning(f"Error parsing Google JWT credential: {e}")

    if not email:
        raise HTTPException(400, "Valid email required for Google authentication.")

    try:
        user = data.get_or_create_google_user(email=email, name=name, picture=picture, google_id=google_id)
        return user
    except data.DataError as e:
        raise HTTPException(400, str(e))


@app.get("/api/auth/me")
def me(user_id: str):
    user = data.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


# ---------- Doctors & Appointments ----------
@app.get("/api/doctors")
def get_doctors():
    return {"date": data.tomorrow(), "doctors": data.list_doctors()}


@app.get("/api/appointments")
def get_appointments(user_id: str | None = None):
    return data.all_appointments(user_id=user_id)


@app.post("/api/appointments/{appointment_id}/cancel")
async def cancel_from_dashboard(appointment_id: str):
    appt = data.cancel(appointment_id)
    if not appt:
        raise HTTPException(404, "Active appointment not found")
    await events.broadcast({"type": "appointment_cancelled", "appointment": appt})
    return appt


# ---------- WebSockets ----------
@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    events.subscribers.add(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; client sends nothing important
    except WebSocketDisconnect:
        pass
    finally:
        events.subscribers.discard(ws)


@app.websocket("/ws/call")
async def ws_call(ws: WebSocket):
    await ws.accept()
    user_id = ws.query_params.get("user_id") or ws.query_params.get("user_email")
    try:
        await run_pipeline(ws, user_id=user_id)
    except Exception as e:
        logger.exception(f"Call pipeline error: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# Mounted last so /api and /ws routes win. Open http://localhost:8000
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
