"""Webhook route — delegates to MessageGateway.handle_webhook.

POST /webhook/{channel} — accepts incoming webhook from IM channels
(DingTalk/Slack/WeCom/Feishu), verifies signature, dispatches async.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from eaos.gateway.api.deps import get_gateway
from fastapi import APIRouter, Depends, HTTPException, Request

if TYPE_CHECKING:
    from eaos.gateway.im.gateway import MessageGateway

router = APIRouter()


@router.post("/webhook/{channel}")
async def webhook(
    channel: str,
    request: Request,
    gateway: MessageGateway = Depends(get_gateway),  # noqa: B008
) -> dict[str, Any]:
    """Handle incoming webhook from an IM channel."""
    raw = await request.json()
    headers = {k: v for k, v in request.headers.items()}

    result = await gateway.handle_webhook(channel, raw, headers)

    if result.get("status") == "error":
        raise HTTPException(
            status_code=result.get("code", 400),
            detail=result.get("message", "webhook error"),
        )

    return result
