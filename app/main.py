from fastapi import FastAPI

from .observability import configure_tracing
from .routes import router


configure_tracing()
app = FastAPI(title="FastAPI Agent", version="1.0")
app.include_router(router)



