"""The 3 required LLM tools, registered inside the Pipecat pipeline (function calling)."""
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

import data
import events

# ---------------- schemas (what the LLM sees) ----------------
check_schema = FunctionSchema(
    name="check_doctor_availability",
    description="Get free appointment slots. Filter by doctor name and/or speciality, and optionally a date. "
                "Call with no filters to list all doctors.",
    properties={
        "doctor_name": {"type": "string", "description": "e.g. 'Vikram Mehta'"},
        "speciality": {"type": "string",
                       "description": "General Physician, Cardiologist, Dermatologist, Orthopedic Surgeon, Pediatrician, Neurologist, ENT Specialist, Psychiatrist, Gynecologist, or Ophthalmologist"},
        "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to tomorrow."},
    },
    required=[],
)

book_schema = FunctionSchema(
    name="book_appointment",
    description="Book a slot for the caller. Only call AFTER the caller confirmed their phone number "
                "and the exact doctor, date and time.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's phone number, digits as spoken."},
        "phone_confirmed": {"type": "boolean",
                            "description": "True only if you read the number back and the caller said yes."},
        "doctor_id": {"type": "string", "description": "doctor_id from check_doctor_availability, e.g. 'd2'."},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "time": {"type": "string", "description": "24h HH:MM, e.g. '10:30'"},
    },
    required=["phone_number", "phone_confirmed", "doctor_id", "date", "time"],
)

cancel_schema = FunctionSchema(
    name="cancel_appointment",
    description="Cancel the caller's existing appointment, found by their phone number. "
                "Only call AFTER the caller confirmed the number.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's phone number, digits as spoken."},
        "phone_confirmed": {"type": "boolean",
                            "description": "True only if you read the number back and the caller said yes."},
        "appointment_id": {"type": "string",
                           "description": "Needed only if the caller has several appointments."},
    },
    required=["phone_number", "phone_confirmed"],
)

tools_schema = ToolsSchema(standard_tools=[check_schema, book_schema, cancel_schema])

NOT_CONFIRMED = {"error": "Phone number not confirmed. Read it back digit by digit and wait for a yes."}


# ---------------- handlers ----------------
async def check_doctor_availability(params: FunctionCallParams):
    a = params.arguments
    try:
        day = data.parse_date(a.get("date"))
        docs = data.find_doctors(a.get("doctor_name"), a.get("speciality"))
    except data.DataError as e:
        await params.result_callback({"error": str(e)})
        return
    if not docs:
        await params.result_callback({"error": "No matching doctor.", "available_specialities": data.specialities()})
        return
    await params.result_callback({
        "date": day,
        "doctors": [
            {"doctor_id": d["id"], "name": d["name"], "speciality": d["speciality"],
             "available_slots": data.free_slots(d["id"], day)}
            for d in docs
        ],
    })


def register_tools(llm, user_id: str | None = None) -> None:
    async def _book_appointment(params: FunctionCallParams):
        a = params.arguments
        if not a.get("phone_confirmed"):
            await params.result_callback(NOT_CONFIRMED)
            return
        try:
            phone = data.normalize_phone(a.get("phone_number", ""))
            day = data.parse_date(a.get("date"))
            appt = data.book(phone, a.get("doctor_id", ""), day, a.get("time", ""), user_id=user_id)
        except data.DataError as e:
            await params.result_callback({"error": str(e)})
            return
        await events.broadcast({"type": "appointment_booked", "appointment": appt})
        await params.result_callback({"status": "booked", "appointment": appt})

    async def _cancel_appointment(params: FunctionCallParams):
        a = params.arguments
        if not a.get("phone_confirmed"):
            await params.result_callback(NOT_CONFIRMED)
            return
        try:
            phone = data.normalize_phone(a.get("phone_number", ""))
        except data.DataError as e:
            await params.result_callback({"error": str(e)})
            return

        active = data.active_appointments(phone, user_id=user_id)
        if not active:
            await params.result_callback({"error": "No active appointment found for this phone number."})
            return

        target = None
        if a.get("appointment_id"):
            target = next((x for x in active if x["id"] == a["appointment_id"]), None)
        elif len(active) == 1:
            target = active[0]
        if target is None:
            await params.result_callback({
                "status": "need_choice",
                "message": "Caller has several appointments. Ask which one, then call again with appointment_id.",
                "appointments": active,
            })
            return

        cancelled = data.cancel(target["id"])
        await events.broadcast({"type": "appointment_cancelled", "appointment": cancelled})
        await params.result_callback({"status": "cancelled", "appointment": cancelled})

    llm.register_function("check_doctor_availability", check_doctor_availability)
    llm.register_function("book_appointment", _book_appointment)
    llm.register_function("cancel_appointment", _cancel_appointment)
