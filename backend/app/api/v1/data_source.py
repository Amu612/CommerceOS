"""Active Data Source: switch every agent between the historic Olist/DataCo
dataset and a live Shopify store, without ever blending the two."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.data_source_service import data_source_service

router = APIRouter(prefix="/api/v1/data-source", tags=["Data Source"])


class SelectSourceBody(BaseModel):
    source: str


@router.get("")
def get_data_source():
    return {
        "active": data_source_service.get_active_source(),
        "available": data_source_service.available_sources(),
    }


@router.post("/select")
def select_data_source(body: SelectSourceBody):
    from app.exceptions.base import ValidationException
    from app.services.event_bus import publish_event

    try:
        active = data_source_service.set_active_source(body.source)
    except ValueError as exc:
        raise ValidationException(str(exc))

    publish_event("data_source", {"type": "data_source_changed", "active": active})
    return {"active": active, "available": data_source_service.available_sources()}
