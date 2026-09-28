from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from typesafe_sdk import AsyncTypeSafeClient

from app.routers import chat, sessions, voice
from app.settings import get_settings
from app.text_agent import bind


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    openai = AsyncOpenAI(api_key=settings.openai_api_key)
    jev = AsyncTypeSafeClient(api_key=settings.jev_api_key)
    bind(openai, jev)
    try:
        yield
    finally:
        await openai.close()
        await jev.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(voice.router)
    return app


app = create_app()
