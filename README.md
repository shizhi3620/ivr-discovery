# IVR Tree Discovery

Automated IVR phone tree explorer. Enter an authorized phone number and the system places real calls to discover and map out the menu structure as an interactive tree.

**Live demo: https://ivr-tree-discovery-production.up.railway.app/**

## How It Works

1. **Root call**: The default Android SIM gateway places a real cellular call and records the IVR audio
2. **Transcript parsing**: Tencent Cloud ASR transcribes the 8 kHz recording; DeepSeek extracts structured menu options (DTMF keys + labels)
3. **BFS exploration**: Child nodes are queued in a priority queue (sorted by depth) and explored by a pool of 3 concurrent workers — true breadth-first traversal
4. **Branch navigation**: For each DTMF option, a new call is placed and the key is injected after the greeting. Voice-only options are synthesized to 8 kHz WAV with Tencent TTS and played into the call
5. **Cycle detection**: Menus are fingerprinted by their option labels. Jaccard similarity (threshold 0.6) catches cases where the same IVR menu is paraphrased differently across calls
6. **Live tree updates**: Node states stream over WebSocket while calls run. Tencent file ASR is asynchronous, so the full transcript arrives after the call ends

## Features

- **Interactive tree visualization** with React Flow + dagre auto-layout
- **Click any node** to see its full transcript, parsed options, call cost, and status
- **Re-discover subtrees** — click re-discover on any node to re-explore that branch
- **Handles edge cases**: dead ends, busy lines (retry with backoff), short transcripts, voice-based IVRs, compound DTMF paths (depth 2+)
- **Session persistence** — refresh the page and your tree is restored from SQLite
- **Cost tracking** — provider-reported call costs are displayed; the Android SIM path currently reports zero because carrier/SIM costs are not itemized

## Architecture

```
Browser (React + React Flow)
    ↕ WebSocket
FastAPI Backend
    → Telephony Provider (pluggable)
        ├── AndroidSimGatewayProvider — China mainland (default): FreeSWITCH ESL + WAV recording
        │     → rooted Android phone (SIM gateway) → carrier IVR
        │     → Audio Provider: Tencent Cloud 8k ASR / TTS
        └── BlandProvider — non-default: Bland AI places calls + returns transcript
    → DeepSeek (parses transcripts into structured menu options; Anthropic optional)
    → SQLite (persists sessions, nodes, edges)
```

Telephony is behind a Provider seam (`backend/providers/`), selected with
`TELEPHONY_PROVIDER` (`android_sim` default, or `bland`). Providers declare their
capabilities, because they are not interchangeable: Bland returns its own ASR
transcript, while the Android SIM gateway records audio and delegates ASR/TTS to
the separate Audio Provider. See
[docs/adr/0004](docs/adr/0004-telephony-provider-boundary.md) and the
[Tencent Cloud API note](docs/research/tencent-cloud-8k-asr-tts.md).

See [architecture.md](architecture.md) for the full system design with sequence diagrams.

## Tech Stack

- **Backend**: Python 3.12, FastAPI, asyncio, aiosqlite
- **Frontend**: React 18, TypeScript, Vite, React Flow, Dagre, Tailwind CSS
- **AI**: DeepSeek (default), Anthropic optional
- **Audio**: Tencent Cloud `8k_zh` ASR + `TextToVoice` TTS
- **Telephony**: rooted Android SIM gateway via FreeSWITCH (default), Bland optional
- **Data**: SQLite (zero-config, file-based)
- **Realtime**: WebSocket (bidirectional, single connection)

## Setup

```bash
# Backend
cd backend
cp .env.example .env  # Add DeepSeek and Tencent Cloud credentials
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

For the China mainland path, configure FreeSWITCH, register the Android gateway,
and start FreeSWITCH before the backend. See
[gateway/freeswitch/README.md](gateway/freeswitch/README.md).

## Project Structure

```
backend/
├── main.py              # FastAPI app, WebSocket handler, REST endpoints
├── discovery.py         # Worker pool, BFS orchestration, cycle detection
├── bland_client.py      # Bland AI API client, call management
├── transcript_parser.py # AI Provider powered transcript → structured options
├── ai/                  # DeepSeek / Anthropic text-model boundary
├── audio/               # Tencent Cloud ASR/TTS boundary
├── providers/           # Telephony Provider boundary
├── database.py          # SQLite CRUD (aiosqlite)
├── models.py            # Pydantic models + SQL schema
└── tests/               # pytest suite

frontend/
├── src/
│   ├── App.tsx           # Main app, WebSocket message handler
│   ├── hooks/useWebSocket.ts
│   └── components/
│       ├── Controls.tsx  # Phone input, Discover/Stop/Clear
│       ├── TreeView.tsx  # React Flow canvas with dagre layout
│       ├── IVRNode.tsx   # Custom node component
│       └── NodeDetail.tsx # Side panel with transcript + options
└── vite.config.ts
```
