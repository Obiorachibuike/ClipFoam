"""Aggregate API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    analytics,
    auth,
    brand_kits,
    candidates,
    clips,
    content,
    files,
    jobs,
    media,
    projects,
    social,
    system,
    transcripts,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(media.router)
api_router.include_router(transcripts.router)
api_router.include_router(candidates.router)
api_router.include_router(clips.router)
api_router.include_router(content.router)
api_router.include_router(brand_kits.router)
api_router.include_router(social.router)
api_router.include_router(jobs.router)
api_router.include_router(analytics.router)
api_router.include_router(system.router)
api_router.include_router(files.router)
