# Onboarding Chat

A conversational onboarding experience that collects profile information through natural text chat and an optional voice call — without feeling like a form.

The assistant introduces itself, learns what the user wants help with, gathers a few facts (nickname, name, Gmail, use case), and can transition into a simulated phone call before the user graduates into the main product.

## How it works

### Models

| Channel | Model | Role |
|---------|-------|------|
| Text chat | `gpt-5.4-nano` | Draft replies, extract slots from user messages |
| Voice call | `gpt-live-1` | Real-time spoken conversation over WebSocket |

Text uses the fastest OpenAI model for low-latency turns. Voice uses GPT Live for natural, interruptible speech.

### System 1 steering (JEV gate)

A **System 1 decision model** ([TypeSafe / JEV](https://github.com/typesafe-ai/typesafe-sdk)) acts as a harness and evaluator on every draft response. It runs a battery of checks and steers the assistant until the reply meets our standards.

**Rewrite loop.** Each draft is scored against criteria like:

- **On track** — stays focused on onboarding
- **Off topic** — acknowledges tangents and steers back
- **Conversational** — sounds like a person, not a form or script
- **One ask** — requests at most one new detail per turn
- **Asked chosen** — asks for the right fact for this moment in the conversation
- **Collection complete** — handles graduation and completion correctly
- **Connect Gmail** — only mentions the Connect button when an address is already stored

When any check fails, the gate injects specific feedback and the text model **rewrites the response**. This loop continues until all checks pass (or one rewrite is attempted, depending on the path).

**Dynamic next question.** The same System 1 pass also classifies which single piece of information to ask for next (`agent_name`, `user_name`, `gmail`, `help_with`, or `none`). The classifier reads the full transcript and what is already collected — it does **not** follow a fixed script order. That keeps the conversation feeling natural instead of like a multi-step form.

### Onboarding flow

1. User texts first; the assistant introduces itself and asks one useful question.
2. The assistant asks permission to call.
3. If the user agrees, they see **Pick up** / **Don't pick up** (simulated incoming call).
4. On a voice call, the assistant collects name, email, and help topic conversationally.
5. Back in chat, the user can name the assistant, connect Gmail, and fill any remaining gaps.
6. When enough is collected (or the user is ready to start), they graduate.

Collected facts are shown in a live panel on the right side of the UI.

## What's simulated

These integrations are stubbed for the scope of this project:

- **Gmail OAuth** — The **Connect Gmail** button marks the stored address as connected via `POST /sessions/{id}/gmail`. There is no real Google OAuth flow.
- **Phone call** — Calls happen in-browser over WebSocket to GPT Live, not through a telephony provider (Twilio, etc.). Pick up / decline is a UI simulation of an incoming call.

## Project structure

```
onboarding-chat-1/
├── run-dev.sh              # Start backend + frontend together
├── onboarding-backend/     # FastAPI API, text agent, voice bridge, JEV gate
│   └── app/
│       ├── text_agent.py   # Text turns, slot extraction, rewrite loop
│       ├── live_bridge.py  # GPT Live voice session
│       ├── jev_gate.py     # System 1 checks and next-info classification
│       └── profile.py      # Onboarding facts and instructions
└── onboarding-frontend/    # Next.js chat UI, call stage, collected panel
```

## Prerequisites

- **Python 3.11+** (backend uses a virtualenv at `onboarding-backend/.venv`)
- **Node.js 18+** and npm
- API keys:
  - [OpenAI API key](https://platform.openai.com/api-keys) (text + voice)
  - [JEV / TypeSafe API key](https://typesafe.ai) (System 1 gate)

## Setup

### 1. Backend

```bash
cd onboarding-backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `onboarding-backend/.env`:

```env
OPENAI_API_KEY=sk-...
JEV_API_KEY=...
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

### 2. Frontend

```bash
cd onboarding-frontend
npm install
cp .env.example .env.local
```

The default `NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8000` is usually fine.

## Run locally

From the repo root, start both services with one script:

```bash
chmod +x run-dev.sh   # first time only
./run-dev.sh
```

This launches:

| Service | URL |
|---------|-----|
| Backend API | http://127.0.0.1:8000 |
| Frontend app | http://localhost:3000 |

Press `Ctrl+C` to stop both. The script tears down child processes on exit.

### Run separately (optional)

```bash
# Terminal 1 — backend
cd onboarding-backend
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2 — frontend
cd onboarding-frontend
npm run dev -- --port 3000
```

## Tests

```bash
cd onboarding-backend
source .venv/bin/activate
pytest
```

Tests cover the JEV gate (rewrite behavior, next-info selection), chat flows, profile logic, and voice gate helpers.

## What I'd do next

- **Configurable onboarding** — Make questions and fields data-driven so the team can A/B test different onboarding flows without code changes.
- **Real integrations** — Replace simulated Gmail OAuth and in-browser calling with production OAuth and telephony (or native app voice).
- **Ops and observability** — Log gate failures and rewrite rates to tune criteria and measure conversation quality.

## License

MIT — see [LICENSE](LICENSE).
