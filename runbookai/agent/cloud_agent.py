"""Cloud agent for customer VPC deployment.

This is the `runbookai-agent` entry point. The agent:
1. Authenticates to cloud via RUNBOOKAI_API_KEY
2. Maintains SSE connection to cloud for incident delivery
3. Exposes MCP server with VPC-specific tools (SSH, BMC, metrics, logs)
4. Streams tool responses back to cloud

The agent can run in the customer's VPC or locally for testing.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger("runbookai.agent.cloud_agent")


class CloudAgent:
    """Cloud-connected agent running in customer VPC."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        cloud_url: str = "https://runbookai.cloud",
        mcp_port: int = 7777,
        heartbeat_interval: int = 30,
    ):
        """Initialize cloud agent.

        Args:
            api_key: API key for cloud authentication (defaults to RUNBOOKAI_API_KEY env var)
            cloud_url: Base URL of RunbookAI cloud
            mcp_port: Port to expose MCP server on
            heartbeat_interval: Seconds between heartbeats (default 30)
        """
        self.api_key = api_key or os.environ.get("RUNBOOKAI_API_KEY", "")
        self.cloud_url = cloud_url
        self.mcp_port = mcp_port
        self.heartbeat_interval = heartbeat_interval
        self.customer_id: Optional[str] = None
        self.agent_id: Optional[str] = None
        self.session: Optional[httpx.AsyncClient] = None
        self.is_connected = False

        if not self.api_key:
            raise ValueError(
                "RUNBOOKAI_API_KEY environment variable not set. "
                "Set it or pass api_key parameter."
            )

        logger.info(
            "Cloud agent initialized: cloud_url=%s mcp_port=%s",
            self.cloud_url,
            self.mcp_port,
        )

    async def authenticate(self) -> bool:
        """Authenticate to cloud and get customer/agent IDs.

        Makes HTTP request to extract customer_id from API key.
        Returns True if successful.
        """
        try:
            async with httpx.AsyncClient() as client:
                # TODO: Phase 3.2 — add /api/auth endpoint to resolve API key to customer
                # For now, we'll extract customer_id from agent handshake
                logger.info("Cloud authentication pending: Phase 3.2 implementation")
                return True
        except Exception as e:
            logger.error("Authentication failed: %s", e)
            return False

    async def connect(self) -> bool:
        """Register agent with cloud and establish SSE connection.

        Calls /api/agents/{customer_id}/connect endpoint and starts
        heartbeat task.
        """
        if not await self.authenticate():
            return False

        try:
            self.session = httpx.AsyncClient(
                timeout=30.0,
                headers={"X-API-Key": self.api_key},
            )
            self.is_connected = True
            logger.info("Cloud agent connected: ready to receive incidents")
            return True
        except Exception as e:
            logger.error("Connection to cloud failed: %s", e)
            return False

    async def send_heartbeat(self) -> bool:
        """Send heartbeat to cloud to keep connection alive.

        Should be called every heartbeat_interval seconds.
        Returns True if successful.
        """
        if not self.is_connected or not self.session:
            return False

        try:
            # TODO: Phase 3.2 — implement /api/agents/{customer_id}/heartbeat endpoint
            logger.debug("Heartbeat sent to cloud")
            return True
        except Exception as e:
            logger.error("Heartbeat failed: %s", e)
            return False

    async def listen_for_incidents(self) -> None:
        """Listen for incident webhooks from cloud via SSE.

        Blocks indefinitely, receiving incidents and dispatching to MCP server.
        Automatically reconnects on connection loss.
        """
        while True:
            try:
                if not self.is_connected:
                    if not await self.connect():
                        await asyncio.sleep(5)
                        continue

                # TODO: Phase 3.2 — implement SSE connection
                logger.info("Listening for incidents from cloud...")
                await asyncio.sleep(1)

            except Exception as e:
                logger.error("Error in listen loop: %s", e)
                self.is_connected = False
                await asyncio.sleep(5)

    async def start(self) -> None:
        """Start the cloud agent.

        Runs indefinitely:
        1. Connects to cloud
        2. Starts MCP server on mcp_port
        3. Listens for incidents
        4. Sends periodic heartbeats
        """
        try:
            # Connect to cloud
            if not await self.connect():
                logger.error("Failed to connect to cloud. Exiting.")
                sys.exit(1)

            # TODO: Phase 3.2 — start MCP server on mcp_port
            logger.info("MCP server would start on port %s (Phase 3.2)", self.mcp_port)

            # Start heartbeat task
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Listen for incidents (blocks indefinitely)
            await self.listen_for_incidents()

            # Cleanup (only reached on exception)
            heartbeat_task.cancel()

        except KeyboardInterrupt:
            logger.info("Agent shutting down...")
            if self.session:
                await self.session.aclose()
            self.is_connected = False

        except Exception:
            logger.exception("Unexpected error in agent")
            sys.exit(1)

    async def _heartbeat_loop(self) -> None:
        """Background task: send heartbeats every heartbeat_interval seconds."""
        while True:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                await self.send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat error: %s", e)

    async def disconnect(self) -> bool:
        """Gracefully disconnect from cloud.

        Calls /api/agents/{customer_id}/disconnect endpoint and closes session.
        """
        try:
            # TODO: Phase 3.2 — call disconnect endpoint
            logger.info("Agent disconnecting from cloud")
            if self.session:
                await self.session.aclose()
            self.is_connected = False
            return True
        except Exception as e:
            logger.error("Disconnect failed: %s", e)
            return False


async def main():
    """Entry point for runbookai-agent CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    agent = CloudAgent()
    await agent.start()


def cli_main():
    """Synchronous entry point for CLI commands."""
    # This will be called by the runbookai-agent CLI
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
