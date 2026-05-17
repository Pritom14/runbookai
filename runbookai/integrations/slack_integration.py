"""Slack integration module — incident notifications and alerting.

This module provides utilities for testing Slack webhook connectivity
and manages all Slack-related integration functionality.

Requires SLACK_WEBHOOK_URL in config. No-ops gracefully if not set.
"""

import logging

import httpx

logger = logging.getLogger("runbookai.integrations.slack_integration")


async def test_webhook(webhook_url: str) -> dict:
    """Test Slack webhook connectivity.

    Args:
        webhook_url: Slack webhook URL

    Returns:
        {
            "success": bool,
            "status": str,
            "message": str,
        }
    """
    if not webhook_url:
        return {
            "success": False,
            "status": "error",
            "message": "No webhook URL provided",
        }

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":white_check_mark: RunbookAI Slack Integration Test",
                    "emoji": True,
                },
            },
            {
                "type": "divider",
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": "*Status:*\nWebhook is configured and working!",
                    },
                    {
                        "type": "mrkdwn",
                        "text": "*Next Steps:*\nIncident notifications will appear here.",
                    },
                ],
            },
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info("Slack test: POST webhook")
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 200:
                logger.info("Slack test: webhook connectivity successful")
                return {
                    "success": True,
                    "status": "ok",
                    "message": "Slack webhook is accessible and working",
                }
            else:
                logger.error(
                    "Slack test: webhook returned %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return {
                    "success": False,
                    "status": "error",
                    "message": f"Webhook error: {resp.status_code}",
                }
    except Exception as e:
        logger.error("Slack test: connection error — %s", str(e))
        return {
            "success": False,
            "status": "error",
            "message": f"Connection error: {str(e)}",
        }
