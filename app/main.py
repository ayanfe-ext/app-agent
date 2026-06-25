from fastapi import FastAPI

from .observability import configure_tracing
from .routes import router


configure_tracing()
app = FastAPI(title="FastAPI Agent (GROQ)")
app.include_router(router)



