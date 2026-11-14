"""Connects to the payer-systems MCP server over stdio and loads its tools for LangChain."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app import settings

READ_TOOLS = {"find_member", "check_eligibility", "verify_provider", "find_policy"}
WRITE_TOOLS = {"create_pa_case", "route_to_queue", "attach_document"}

_client = MultiServerMCPClient({
    "payer": {"transport": "stdio", "command": sys.executable, "args": ["-m", "app.payer_mcp.server"], "cwd": str(settings.ROOT)}
})


@asynccontextmanager
async def payer_tools():
    """One MCP session (one server process) with all tools, keyed by name."""
    async with _client.session("payer") as session:
        tools = await load_mcp_tools(session)
        yield {t.name: t for t in tools}
