"""CLI commands for cloud agent configuration and management.

This module provides the CLI interface for customers to:
- Configure cloud API key
- Start the agent
- Check agent status
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import click

logger = logging.getLogger("runbookai.agent.cloud_cli")


def get_config_dir() -> Path:
    """Get the user's RunbookAI config directory (~/.runbookai)."""
    config_dir = Path.home() / ".runbookai"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    """Get the path to the RunbookAI config file (~/.runbookai/config.json)."""
    return get_config_dir() / "config.json"


def load_config() -> dict:
    """Load RunbookAI config from disk."""
    config_file = get_config_file()
    if not config_file.exists():
        return {}
    try:
        with open(config_file) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load config: %s", e)
        return {}


def save_config(config: dict) -> None:
    """Save RunbookAI config to disk."""
    config_file = get_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)
    logger.info("Config saved: %s", config_file)


@click.group()
def cli():
    """RunbookAI cloud agent CLI."""
    pass


@cli.command()
@click.option("--api-key", prompt="API Key", hide_input=True, help="Cloud API key (sk_...)")
@click.option("--cloud-url", default="https://runbookai.cloud", help="Cloud API URL")
@click.option("--mcp-port", default=7777, type=int, help="MCP server port")
def config(api_key: str, cloud_url: str, mcp_port: int) -> None:
    """Configure the cloud agent with API key and settings."""
    if not api_key.startswith("sk_"):
        click.echo("Error: API key should start with 'sk_'", err=True)
        return

    cfg = load_config()
    cfg.update({
        "api_key": api_key,
        "cloud_url": cloud_url,
        "mcp_port": mcp_port,
    })
    save_config(cfg)

    click.secho("Configuration saved:", fg="green")
    click.echo(f"  Cloud URL: {cloud_url}")
    click.echo(f"  MCP Port: {mcp_port}")
    click.echo("\nNext: run 'runbookai agent start'")


@cli.command()
def start() -> None:
    """Start the cloud agent."""
    from runbookai.agent.cloud_agent import CloudAgent

    cfg = load_config()
    api_key = cfg.get("api_key") or os.environ.get("RUNBOOKAI_API_KEY")

    if not api_key:
        click.echo(
            "Error: API key not configured. Run 'runbookai config' first.",
            err=True,
        )
        return

    cloud_url = cfg.get("cloud_url", "https://runbookai.cloud")
    mcp_port = cfg.get("mcp_port", 7777)

    click.echo(f"Starting RunbookAI cloud agent...")
    click.echo(f"  Cloud: {cloud_url}")
    click.echo(f"  MCP Port: {mcp_port}")
    click.echo("")
    click.echo("Agent is running. Press Ctrl+C to stop.")

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Import here to avoid circular dependency
    import asyncio

    agent = CloudAgent(
        api_key=api_key,
        cloud_url=cloud_url,
        mcp_port=mcp_port,
    )

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        click.echo("\nAgent stopped.")


@cli.command()
def status() -> None:
    """Check agent status and configuration."""
    cfg = load_config()

    if not cfg:
        click.echo("Not configured. Run 'runbookai config' first.")
        return

    api_key = cfg.get("api_key", "")
    cloud_url = cfg.get("cloud_url", "https://runbookai.cloud")
    mcp_port = cfg.get("mcp_port", 7777)

    click.echo("RunbookAI Cloud Agent Configuration:")
    click.echo(f"  Cloud URL: {cloud_url}")
    click.echo(f"  MCP Port: {mcp_port}")
    click.echo(f"  API Key: {api_key[:10]}..." if api_key else "  API Key: Not set")

    # TODO: Phase 3.2 — check actual connection status
    click.echo("\nAgent Status:")
    click.echo("  Status checking: Phase 3.2 implementation")


@cli.command()
def logs() -> None:
    """Show agent logs."""
    config_dir = get_config_dir()
    logs_file = config_dir / "agent.log"

    if not logs_file.exists():
        click.echo("No logs available.")
        return

    try:
        with open(logs_file) as f:
            click.echo(f.read())
    except Exception as e:
        click.echo(f"Error reading logs: {e}", err=True)


if __name__ == "__main__":
    cli()
