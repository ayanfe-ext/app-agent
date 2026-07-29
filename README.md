# FastAPI Agent

This project provides a FastAPI payment-operations agent. It asks a configured LLM for a
structured next-step decision, validates tool arguments with Pydantic, asks for
confirmation before money-moving actions, executes registered tools, and stores
conversation state in SQLite.

Quick start

1. Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and set values as needed:

```env
LLM_PROVIDER=groq
LLM_MODEL=
LLM_BASE_URL=
GROQ_CONSOLE_URL=
GROQ_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
HOST=0.0.0.0
PORT=8000
GROQ_MODEL=llama-3.3-70b-versatile
DUPLO_CHECKOUT_URL=
DUPLO_API_KEY=
DUPLO_BASE_URL=
DUPLO_PAYOUT_URL=
APP_API_KEY=
MERCHANT_API_KEY=
RATE_LIMIT_PER_MINUTE=0
JWT_SECRET_KEY=change-me-in-production
JWT_ALGORITHM=HS256
JWT_EXP_MINUTES=60
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
ATLAS_WEBHOOK_VERIFY=false
ARIZE_ENABLED=false
ARIZE_SPACE_ID=
ARIZE_API_KEY=
ARIZE_PROJECT_NAME=fastapi-payment-agent
ARIZE_LOG_TO_CONSOLE=false
```

`APP_API_KEY` protects customer checkout access and `MERCHANT_API_KEY` protects
merchant access. The frontend calls `POST /auth/login` with one of those keys
and receives a short-lived bearer token. Legacy `X-API-Key` headers still work
for direct API testing. `RATE_LIMIT_PER_MINUTE=0` disables the in-process rate
limiter.

Set `LLM_PROVIDER=groq` or `LLM_PROVIDER=openai` to switch model providers.
Use `LLM_MODEL` to override the provider default model. The agent only supports
Nigerian Naira for payment tools; naira aliases are normalized to `NGN`, and
other currencies are rejected before tool execution.

Customer checkout stays on `POST /conversation`. Merchant chat uses
`POST /merchant/conversation`, which can create checkout links, create payouts,
and search payout history. Direct merchant utility endpoints like
`POST /merchant/payout`, `GET /merchant/payout/transactions`, and
`GET /merchant/payout/status/{source_reference}` require merchant access.

Atlas payout webhooks can post payout lifecycle events to
`POST /merchant/payout/webhook`. The app stores the latest event by transaction
reference and keeps terminal states (`OUT_FLOW_SUCCESS_EVENT` and
`OUT_FLOW_FAILED_EVENT`) from being overwritten by late pending events. Set
`ATLAS_WEBHOOK_VERIFY=true` to verify webhook references through Atlas before
recording them.

Arize AX observability is optional. Set `ARIZE_ENABLED=true`,
`ARIZE_SPACE_ID`, and `ARIZE_API_KEY` to export OpenTelemetry traces to AX.
`ARIZE_PROJECT_NAME` controls the project name shown in Arize. Set
`ARIZE_LOG_TO_CONSOLE=true` while debugging instrumentation locally.

3. Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open Swagger at `http://127.0.0.1:8000/docs` to try the endpoints.

Frontend

The React app lives in `frontend/`.

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open `http://127.0.0.1:5173`, choose customer or merchant mode, paste the
matching access key, and start chatting. The frontend sends customer messages to
`/conversation` and merchant messages to `/merchant/conversation`. Merchant
payout result cards can track status through `/merchant/payout/status/{source_reference}`.

Main flow

1. `POST /conversation` receives a user message.
2. The agent asks the model for a structured decision.
3. If details are missing, the assistant asks for the next detail.
4. If a tool is ready, the backend validates arguments and asks for confirmation.
5. After the user confirms, the registered tool handler runs.
