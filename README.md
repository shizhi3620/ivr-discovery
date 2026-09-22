# IVR Tree Discovery

Automated IVR phone tree explorer. Enter any phone number and the system places real calls to discover and map out the entire menu structure as an interactive tree — in real time.

**Live demo: https://ivr-tree-discovery-production.up.railway.app/**

## How It Works

1. **Root call**: An AI agent calls the number and listens silently to the IVR greeting and menu options
2. **Transcript parsing**: Claude Sonnet analyzes the call transcript and extracts structured menu options (DTMF keys + labels)
3. **BFS exploration**: Child nodes are queued in a priority queue (sorted by depth) and explored by a pool of 3 concurrent workers — true breadth-first traversal
4. **Branch navigation**: For each menu option, a new call is placed where the agent intelligently waits for the menu to finish, then presses the correct key (or speaks the option for voice-based IVRs)
5. **Cycle detection**: Menus are fingerprinted by their option labels. Jaccard similarity (threshold 0.6) catches cases where the same IVR menu is paraphrased differently across calls
6. **Real-time updates**: Everything streams to the browser over a single WebSocket connection — nodes appear and transition through states (pending → calling → parsing → completed) live

## Features

- **Interactive tree visualization** with React Flow + dagre auto-layout
- **Click any node** to see its full transcript, parsed options, call cost, and status
- **Re-discover subtrees** — click re-discover on any node to re-explore that branch
- **Handles edge cases**: dead ends, stale calls (auto-terminated after 15s of silence), busy lines (retry with backoff), voice-based IVRs, compound DTMF paths (depth 2+)
- **Session persistence** — refresh the page and your tree is restored from SQLite
- **Cost tracking** — running total of Bland AI call costs displayed in the status bar

## Architecture

```
Browser (React + React Flow)
    ↕ WebSocket
FastAPI Backend
    → Telephony Provider (pluggable)
        ├── AndroidSimGatewayProvider — China mainland (default): FreeSWITCH ESL
        │     → rooted Android phone (SIM gateway) → carrier IVR
        └── BlandProvider — non-default: Bland AI places calls + returns transcript
    → Claude Sonnet (parses transcripts into structured menu options)
    → SQLite (persists sessions, nodes, edges)
```

Telephony is behind a Provider seam (`backend/providers/`), selected with
`TELEPHONY_PROVIDER` (`android_sim` default, or `bland`). Providers declare their
capabilities, because they are not interchangeable: Bland returns its own ASR
transcript, while the Android SIM gateway only carries audio. See
[docs/adr/0004](docs/adr/0004-telephony-provider-boundary.md).

See [architecture.md](architecture.md) for the full system design with sequence diagrams.

## Tech Stack

- **Backend**: Python 3.12, FastAPI, asyncio, aiosqlite
- **Frontend**: React 18, TypeScript, Vite, React Flow, Dagre, Tailwind CSS
- **AI**: Claude Sonnet (transcript parsing), Bland AI (telephony)
- **Data**: SQLite (zero-config, file-based)
- **Realtime**: WebSocket (bidirectional, single connection)

## Setup

```bash
# Backend
cd backend
cp .env.example .env  # Add your BLAND_API_KEY and ANTHROPIC_API_KEY
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Project Structure

```
backend/
├── main.py              # FastAPI app, WebSocket handler, REST endpoints
├── discovery.py         # Worker pool, BFS orchestration, cycle detection
├── bland_client.py      # Bland AI API client, call management
├── transcript_parser.py # Claude-powered transcript → structured options
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
