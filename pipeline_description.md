# Pipeline description

> Edit this in your own words before submitting.

The browser captures microphone audio, converts it to 16 kHz PCM and streams it over a WebSocket to a FastAPI
server. A Pipecat pipeline runs: Silero VAD detects when the caller stops speaking, ElevenLabs transcribes the
speech, Gemini decides what to say, and ElevenLabs turns the reply into audio streamed back to the browser.

Gemini has three tools registered in the pipeline: `check_doctor_availability`, `book_appointment` and
`cancel_appointment`. They read and write a SQLite database of mock doctors and appointments. The caller is
identified only by the phone number they say. The agent reads it back and waits for a "yes", and the tools
refuse to run unless `phone_confirmed` is true, so the rule is enforced in code and not only in the prompt.

When a tool books or cancels, it pushes an event over a second WebSocket so the dashboard row highlights
immediately. Interrupting the agent mid-sentence stops its audio. I used AI tools while building and tested
the full booking and cancel flows end to end.