from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .observability import configure_tracing
from .routes import router


configure_tracing()
app = FastAPI(title="FastAPI Agent", version="1.0")
origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


