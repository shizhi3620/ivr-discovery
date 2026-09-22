# Design Decisions Log

Key architectural and design decisions made during implementation, with alternatives considered and reasoning.

> China mainland update (2026-09-22): the original sections below describe the
> Bland + Claude demo. The current default is Android SIM + FreeSWITCH, Tencent
> Cloud 8 kHz ASR/TTS and DeepSeek. Bland and Anthropic remain optional
> implementations behind their respective Provider boundaries.

---

## 1. Concurrency Model: Worker Pool with asyncio.Queue vs Recursive asyncio.gather

**Chosen**: Worker pool — fixed number of workers pulling from a shared `asyncio.Queue`

**Alternatives considered**:
- **Recursive `asyncio.gather` with semaphore**: Each node, once explored, spawns `asyncio.gather` for all its children. A semaphore limits concurrent calls. Problem: creates deeply nested gather groups that all compete for the semaphore, making it hard to reason about concurrency and cancel cleanly.
- **Strict BFS (level-by-level)**: Explore all nodes at depth N, wait for all to complete, then explore depth N+1. Simpler but slower — can't start exploring a depth-2 node until ALL depth-1 nodes finish.

**Why worker pool wins**:
- Worker count IS the concurrency limit — no semaphore needed
- Clean cancellation (cancel the worker tasks)
- `queue.join()` naturally waits for all work including dynamically-added children
- Nodes are explored as soon as a worker is free, regardless of depth — maximizes parallelism without exceeding the call limit

---

## 2. Realtime Communication: WebSocket vs Server-Sent Events vs Polling

**Chosen**: Single WebSocket connection per browser session

**Alternatives considered**:
- **SSE (Server-Sent Events)**: Simpler for server→client streaming, but we also need client→server messages (start_discovery, cancel) which would require a separate REST endpoint
- **REST polling**: Frontend polls `/api/status` every N seconds. Higher latency, more requests, worse UX for realtime tree updates

**Why WebSocket wins**: Bidirectional — one channel handles both commands (client→server) and realtime updates (server→client). Node status transitions, live transcripts, and session updates all flow through the same connection.

---

## 3. AI Agent Behavior: Silent Listening vs Active Navigation

**Chosen**: Completely silent agent for root calls; minimal speech for branch navigation

**Alternatives considered**:
- **Active agent**: Says "I'd like to hear all my options" — conversational IVRs interpret this as a request and route to a human agent. Failed with American Airlines.
- **`block_interruptions: true`**: Tried to prevent AI from speaking, but it also blocked the IVR system's own messages, cutting them off mid-sentence.
- **Silent with "no" responses**: Agent stays silent, says "no" only to yes/no questions. IVR systems eventually read their full menu when they get no response.

**Why silent wins**: IVR systems have a fallback behavior — when they get silence, they repeat options or read the full menu. This reliably captures all options without triggering human transfers or confusing conversational IVRs.

**For branch exploration with voice IVRs**: Agent says the exact option phrase (e.g., "billing") and then goes silent to hear the submenu.

---

## 4. Transcript Parsing: Claude with Post-Processing vs Pure LLM vs Rule-Based

**Chosen**: Claude parses transcript to JSON, then Python post-processes (dedup + filter)

**Alternatives considered**:
- **Pure LLM**: Let Claude handle all dedup/filtering in the prompt. Risk: inconsistent behavior, sometimes it filters too aggressively or misses duplicates.
- **Rule-based regex**: Parse "press 1 for X" patterns with regex. Breaks on conversational IVRs that say "you can say billing, or technical support."
- **Claude + structured output (tool_use)**: Use Claude's tool calling for guaranteed JSON. Adds complexity, same result since we validate anyway.

**Why hybrid wins**: Claude handles the ambiguity of natural language transcripts (varied phrasing, conversational IVRs). Python handles deterministic rules: deduplicate options that appear as both DTMF and voice ("say mobile or press 1" → keep only press 1), and filter navigation-only options ("repeat", "go back", "main menu").

---

## 5. DTMF Path Encoding: Concatenated String vs Array vs Nested

**Chosen**: String with `w` separator — e.g., `"1w3"` means press 1, wait, press 3

**Alternatives considered**:
- **Array**: `["1", "3"]` — cleaner programmatically but harder to pass as a single Bland AI `precall_dtmf_sequence` parameter
- **No separator**: `"13"` — ambiguous (is that 1+3 or 13?)

**Why string with `w` wins**: Bland AI's `precall_dtmf_sequence` accepts this exact format natively. The `w` (wait) gives the IVR time to process each keypress before the next one.

---

## 6. Session ID Stability: sessionStorage vs URL Hash vs useState

**Chosen**: `sessionStorage` — persists session ID across HMR reloads but not across tabs

**Alternatives considered**:
- **`useMemo(() => crypto.randomUUID(), [])`**: Creates new ID on every HMR module re-evaluation, causing WebSocket disconnects during development
- **URL hash/query param**: Visible to user, clutters URL
- **localStorage**: Persists too long — old sessions would reconnect on new browser tabs

**Why sessionStorage wins**: Survives Vite HMR reloads (which re-execute module scope but not sessionStorage), naturally clears when tab closes. Development-friendly without leaking stale state.

---

## 7. Tree Visualization: React Flow + Dagre vs D3 vs Custom Canvas

**Chosen**: React Flow with dagre auto-layout

**Alternatives considered**:
- **D3.js tree layout**: More flexible but requires manual rendering, pan/zoom, node interaction. Significant effort for marginal benefit.
- **Custom Canvas/SVG**: Maximum control but enormous implementation cost for basic tree features.
- **Plain HTML/CSS tree**: Simple but no pan/zoom, no drag, no auto-layout.

**Why React Flow + dagre wins**: React Flow provides interactive canvas (pan, zoom, drag) out of the box. Dagre handles hierarchical auto-layout. Integrates naturally with React state — just update nodes/edges arrays and the tree re-renders.

---

## 8. Database: SQLite vs PostgreSQL vs In-Memory Only

**Chosen**: SQLite via aiosqlite

**Alternatives considered**:
- **PostgreSQL**: Overkill for a single-user demo app. Adds deployment dependency.
- **In-memory dict**: Simpler but loses data on server restart, can't query historical sessions.
- **Redis**: Good for ephemeral data but awkward for relational tree structures.

**Why SQLite wins**: Zero-config, file-based, async support via aiosqlite. Perfectly sufficient for single-user demo. Schema provides structure for nodes/edges/sessions. Easy to delete and recreate (`rm ivr_discovery.db`).

---

## 9. Duplicate Option Handling: Parser-Level vs Prompt-Level vs Display-Level

**Chosen**: Both prompt-level (instruct Claude) AND parser-level (Python dedup)

**Alternatives considered**:
- **Prompt-only**: Tell Claude to deduplicate. Unreliable — sometimes still returns both "press 1" and "say mobile" versions.
- **Display-only**: Let frontend hide duplicates. Backend tree would still have unnecessary child nodes, wasting API calls.
- **Parser-only**: Don't mention it to Claude, just dedup in Python. Works but Claude might return them with slightly different labels making matching harder.

**Why both wins**: Claude's prompt instructions reduce duplicates (so labels match better), then Python's `_deduplicate_options()` catches anything Claude missed. Belt and suspenders — deterministic code backs up probabilistic LLM.

---

## 10. Voice IVR Navigation: Separate Call with Speech vs DTMF-Only

**Chosen**: Separate call with a custom task prompt that speaks the option phrase

**Alternatives considered**:
- **DTMF-only**: Skip voice-only options entirely. Misses significant portions of conversational IVR trees.
- **Single call with mid-call commands**: Bland AI doesn't support mid-call task changes or speech injection.

**Why separate call wins**: For voice-only IVR options (no DTMF key), we place a new call where the AI agent says the exact option phrase (e.g., "billing"), then goes silent to hear the submenu. This is the only way to navigate conversational IVRs through Bland AI's API.

---

## 11. Cycle Detection: Fingerprint-Based vs Transcript Similarity vs None

**Chosen**: Fingerprint the set of parsed option labels; skip if already seen anywhere in the tree.

**Alternatives considered**:
- **No detection**: IVR branches often loop back to the main menu. Without detection, we re-explore the same menu at every branch, creating an exponentially growing tree of duplicates.
- **Transcript cosine similarity**: Compare raw transcripts using embeddings. More robust to rewording, but slow (requires embedding calls) and overkill when the parsed options are the same.
- **Parent-only comparison**: Only compare a node's options against its direct parent. Misses cross-branch duplicates (two different branches both loop to the same submenu).
- **DTMF path dedup**: Skip if we've already explored the same DTMF path. Doesn't work because different paths can reach the same menu.

**Why global fingerprint wins**: Simple, fast, and catches all duplicates. A `frozenset` of normalized option labels uniquely identifies a menu. If we've already explored a menu with those exact options anywhere in the tree, exploring it again would just recreate the same subtree. Handles both parent cycles and cross-branch duplicates with zero API cost.

---

## 12. Human Transfer Detection: Dual-Layer (Keyword + LLM)

**Chosen**: Real-time keyword matching during polling + Claude confirmation after parsing

**Alternatives considered**:
- **LLM-only**: Wait for call to finish, then ask Claude if a human answered. Problem: the call hangs for minutes while waiting on hold for a representative, wasting cost.
- **Keyword-only**: Check transcript for "transferring you", "please hold", etc. Cheap and fast but could false-positive on IVRs that mention transfers in their menu descriptions.

**Why dual-layer wins**: Keywords in `wait_for_call` catch transfers immediately — `stop_call()` terminates the call within seconds, saving cost. Claude's `human_transfer` flag in the parser catches subtler cases (like a person answering naturally). The keyword layer is the fast path; the LLM layer is the safety net.

---

## 13. Stale Call Detection: Transcript Stagnation vs Fixed Timeout

**Chosen**: Stop call after 5 consecutive polls (~15s) with no transcript growth

**Alternatives considered**:
- **Fixed `max_duration` only**: Bland AI's `max_duration` should end the call, but the API status sometimes stays `in-progress` even after the IVR hangs up.
- **Short fixed timeout**: Aggressive timeout (e.g., 30s) would miss slow IVRs that take time to read all options.

**Why stagnation detection wins**: Adapts to call length — fast menus finish fast, slow menus get full time. Once the transcript stops growing, the IVR has either hung up or is looping silence. Stopping early saves cost and unblocks the worker for the next node.

---

## 14. Conversational IVR Handling: "What are my options?" (Once)

**Chosen**: Agent says "What are my options?" at most once when the IVR asks an open-ended question, then goes silent.

**Alternatives considered**:
- **Always silent**: Works for DTMF IVRs but conversational IVRs (FedEx) ask "What can I help you with?" and hang up after repeated silence.
- **Active conversation**: Agent engages naturally. Problem: agent said "even more options", "More Information", navigated into submenus, and got stuck in loops for 10+ minutes.
- **Repeat the phrase**: Agent keeps saying "What are my options?" every time prompted. FedEx eventually interpreted this as a request and navigated to tracking.

**Why once-and-silent wins**: One prompt is enough to trigger option listing on conversational IVRs. Going silent immediately after prevents the agent from going off-script and accidentally navigating deeper.

---

## 15. Session Persistence: Recovery on Reload

**Chosen**: Backend `/api/sessions/latest` endpoint + `/api/recover-stuck` for interrupted calls

**Alternatives considered**:
- **Frontend-only state**: Lose everything on reload. Terrible UX during a demo.
- **LocalStorage cache**: Store nodes/edges in browser. Stale data problems when backend state diverges.
- **WebSocket replay**: Server replays all events on reconnect. Complex, requires event sourcing.

**Why REST restore wins**: Simple — on page load, call recover-stuck (fixes nodes left in "calling" by a server restart), then fetch the full session state. Source of truth stays in SQLite. Works across restarts, reloads, and tab closures.

---

## 16. AI Vendor Boundary: DeepSeek Default vs Direct Anthropic Calls

**Chosen**: Keep transcript parsing behind `AIProvider`; use DeepSeek by default for China mainland.

**Alternatives considered**:

- **Direct Anthropic calls**: fast to implement, but unavailable as the default in the target region and couples the parser to one vendor.
- **Local model only**: avoids network dependency, but the target Mac/Jetson hardware is not a reliable baseline for Chinese ASR/IVR reasoning quality.

**Why the boundary wins**: `transcript_parser.py` stays vendor-neutral, DeepSeek works in China with an OpenAI-compatible API, and Anthropic remains selectable for the original demo.

---

## 17. ASR/TTS Placement: Separate Audio Provider vs Vendor Logic Inside Telephony

**Chosen**: A separate `AudioProvider` owns speech processing; the Android telephony provider owns recording, DTMF and playback.

**Alternatives considered**:

- **Call Tencent APIs directly from `AndroidSimGatewayProvider`**: fewer classes, but mixes carrier transport with speech vendors and makes testing require telephone state.
- **Rely on FreeSWITCH modules for ASR/TTS**: keeps audio local, but requires additional services/models and less predictable Mandarin telephone quality.

**Why the boundary wins**: Tencent's `8k_zh_large` and `TextToVoice` can be mocked independently, the transport provider only exposes audio files, and a future local ASR/TTS implementation can replace Tencent without touching FreeSWITCH/ESL code.

---

## 18. Call Recording: Direct Gateway Leg + `uuid_record`

**Chosen**: Keep the verified direct `originate user/gateway1` path, resolve its real channel UUID, then start `uuid_record` on that gateway leg with `RECORD_STEREO=true`.

**Alternatives considered**:

- **A-leg `execute_on_answer=record_session` with `&park()`**: attached the recorder to the wrong API-originated leg.
- **B-leg `origination_execute_on_answer`**: not supported by the deployed FreeSWITCH build.
- **Loopback + dialplan `record_session`**: added another routing layer and did not solve channel correlation.
- **Record only on the phone**: the upstream Android gateway does not provide a stable per-call recording contract.

**Why direct `uuid_record` wins**: it records the same gateway leg that already carries DTMF and GSM audio, and channel resolution can be verified through ESL. The missing WAV encoder was the real blocker: Homebrew FreeSWITCH did not load `mod_sndfile`, so `record_session`/`uuid_record` silently produced no file until that module was enabled.
