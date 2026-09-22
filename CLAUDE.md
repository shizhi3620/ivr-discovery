# IVR Tree Discovery System

## Project Overview
A system that calls IVR (Interactive Voice Response) phone trees, discovers their menu structure, and visualizes the tree in realtime. The supported first-stage deployment is China mainland: Android SIM telephony with Tencent Cloud 8k ASR/TTS and DeepSeek transcript parsing.

## Tech Stack
- **Backend**: Python + FastAPI
- **Frontend**: React (Vite) + React Flow (tree visualization)
- **Realtime**: WebSockets (FastAPI native ↔ React)
- **Database**: SQLite
- **AI**: DeepSeek by default; Anthropic optional
- **Audio**: Tencent Cloud `8k_zh_large` ASR and `TextToVoice` TTS
- **Telephony**: rooted Android SIM gateway via FreeSWITCH by default
- **Discovery**: BFS with concurrency limits
- **Testing**: pytest (backend), Vitest (frontend)

## Architecture
```
React (Vite) ←— WebSocket —→ FastAPI
                                 ├── Telephony Provider (android_sim default | bland)
                                 ├── Audio Provider (Tencent ASR/TTS)
                                 ├── AI Provider (DeepSeek default | Anthropic)
                                 └── SQLite (persist tree state)
```

Telephony goes through `backend/providers/` — see `docs/adr/0004`. Select with
`TELEPHONY_PROVIDER` (`android_sim` default, `bland` for the original demo).

Speech processing goes through `backend/audio/`; the Android SIM path requires
Tencent Cloud credentials and records each call as WAV before submitting it to
file-based ASR. See `docs/research/tencent-cloud-8k-asr-tts.md`.

## Key APIs
- **Bland AI**: Docs at https://docs.bland.ai
  - `POST /v1/calls` — place a call with `task` (agent prompt) and `precall_dtmf_sequence` (navigate IVR)
  - `GET /v1/calls/{id}` — get transcript, status, cost
  - `webhook` + `webhook_events` — realtime call events
  - Use base model (not turbo) for IVR navigation support
- **DeepSeek**: Parse call transcripts into structured menu options (Anthropic remains optional)
- **Tencent Cloud ASR/TTS**: Transcribe 8 kHz IVR audio and synthesize voice options

## Discovery Strategy
1. **Root call**: Record the target IVR greeting and menu without speaking
2. **Parse**: Send transcript to DeepSeek → extract menu options (e.g. "Press 1 for Billing")
3. **Branch calls**: Call again and inject the DTMF path after the greeting; voice-only options use Tencent TTS
4. **BFS**: Explore all options at each level in parallel before going deeper
5. **Repeat**: Parse each submenu transcript, discover next level

## Data Model
- **Session**: discovery run for a phone number (id, phone_number, status, total_cost, created_at)
- **Node**: IVR menu point (id, session_id, parent_id, dtmf_path, prompt_text, status, call_id, cost)
- **Edge**: menu option (id, from_node_id, to_node_id, dtmf_key, label)
- **Call metadata**: transcript, provider call id, provider-reported cost

## WebSocket Protocol
Server → Client:
- `{ type: "node_added", node: {...} }`
- `{ type: "node_updated", nodeId, status, ... }`
- `{ type: "session_status", status, totalCost }`

Client → Server:
- `{ type: "start_discovery", phoneNumber }`
- `{ type: "rediscover_subtree", nodeId }`
- `{ type: "cancel" }`

## Commands
- `cd backend && uvicorn main:app --reload` — run backend
- `cd frontend && npm run dev` — run frontend
- `cd backend && pytest` — run tests

## Project Structure
```
backend/
  main.py              — FastAPI app, WebSocket endpoint
  discovery.py         — BFS discovery engine
  providers/           — telephony Provider boundary (ADR 0004)
    base.py            — TelephonyProvider protocol + CallResult/capabilities
    android_sim_provider.py — FreeSWITCH ESL → Android SIM gateway (default)
    bland_provider.py  — Bland AI adapter
    esl.py             — minimal FreeSWITCH ESL client
  bland_client.py      — Bland AI API wrapper (used by bland_provider)
  transcript_parser.py — AI Provider based transcript analysis
  ai/                  — DeepSeek / Anthropic boundary
  audio/               — Tencent Cloud ASR/TTS boundary
  models.py            — SQLite models/schema
  tests/
    test_parser.py
    test_discovery.py
    test_providers.py
    test_audio_providers.py
frontend/
  src/
    App.tsx
    components/
      TreeView.tsx     — React Flow tree visualization
      NodeDetail.tsx   — Node detail panel
      Controls.tsx     — Phone number input, start/cancel
    hooks/
      useWebSocket.ts  — WebSocket connection hook
    types.ts
```

## Style & Conventions
- AI coding tools are used but all code should be explainable
- Focus on: system design, separation of concerns, realtime updates, UI clarity, resilience, test coverage, API efficiency, code clarity
- Keep modules small and single-purpose
- Type hints in Python, TypeScript in React
