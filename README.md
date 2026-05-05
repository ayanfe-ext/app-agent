# FastAPI Agent

This project provides a FastAPI payment-operations agent. It asks Groq for a
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
GROQ_CONSOLE_URL=
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
DUPLO_CHECKOUT_URL=
DUPLO_API_KEY=
CONVERSATION_DB_PATH=conversations.sqlite3
APP_API_KEY=
RATE_LIMIT_PER_MINUTE=0
ARIZE_ENABLED=false
ARIZE_SPACE_ID=
ARIZE_API_KEY=
ARIZE_PROJECT_NAME=fastapi-payment-agent
ARIZE_LOG_TO_CONSOLE=false
```

`APP_API_KEY` is optional. If set, requests to `/conversation` must include
`X-API-Key`. `RATE_LIMIT_PER_MINUTE=0` disables the in-process rate limiter.

Arize AX observability is optional. Set `ARIZE_ENABLED=true`,
`ARIZE_SPACE_ID`, and `ARIZE_API_KEY` to export OpenTelemetry traces to AX.
`ARIZE_PROJECT_NAME` controls the project name shown in Arize. Set
`ARIZE_LOG_TO_CONSOLE=true` while debugging instrumentation locally.

3. Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open Swagger at `http://127.0.0.1:8000/docs` to try the endpoints.

Main flow

1. `POST /conversation` receives a user message.
2. The agent asks the model for a structured decision.
3. If details are missing, the assistant asks for the next detail.
4. If a tool is ready, the backend validates arguments and asks for confirmation.
5. After the user confirms, the registered tool handler runs.
