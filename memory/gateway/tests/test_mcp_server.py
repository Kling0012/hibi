from gateway.app.config import get_settings
from gateway.app.mcp_server import MCP_ALLOWED_HOSTS, build_allowed_hosts, create_mcp
from unittest.mock import AsyncMock

import pytest


def test_mcp_transport_security_allows_only_expected_hosts(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    get_settings.cache_clear()
    security = create_mcp(object()).settings.transport_security
    get_settings.cache_clear()

    assert security.enable_dns_rebinding_protection
    assert security.allowed_hosts == MCP_ALLOWED_HOSTS
    assert "evil.example:*" not in security.allowed_hosts


def test_mcp_transport_security_appends_configured_hosts():
    hosts = build_allowed_hosts("memory.example.internal, 203.0.113.10")

    assert hosts[: len(MCP_ALLOWED_HOSTS)] == MCP_ALLOWED_HOSTS
    assert "memory.example.internal" in hosts
    assert "memory.example.internal:*" in hosts
    assert "203.0.113.10:*" in hosts


@pytest.mark.asyncio
async def test_shared_policy_is_discoverable_without_workspace_files():
    mcp = create_mcp(object())
    assert "project='all'" in mcp.instructions
    assert "Deletion is prohibited" in mcp.instructions
    assert "continue without" in mcp.instructions
    assert "memory_get" in mcp.instructions
    assert "1200 seconds" not in mcp.instructions
    resources = await mcp.list_resources()
    assert any(str(r.uri) == "memory://usage" for r in resources)
    content = list(await mcp.read_resource("memory://usage"))
    assert content[0].content == mcp.instructions
    assert len(await mcp.list_tools()) == 10
    descriptions = {tool.name: tool.description for tool in await mcp.list_tools()}
    for name in ("memory_remember", "memory_supersede", "session_commit"):
        assert "without waiting" in descriptions[name]
        assert "memory_get" in descriptions[name]


@pytest.mark.asyncio
async def test_query_only_search_defaults_to_shared_collection(monkeypatch):
    monkeypatch.delenv("MEMORY_DEFAULT_USER", raising=False)
    monkeypatch.delenv("MEMORY_DEFAULT_PROJECT", raising=False)
    get_settings.cache_clear()
    service = AsyncMock()
    service.search.return_value = []
    mcp = create_mcp(service)
    await mcp.call_tool("memory_search", {"query": "SSH host setup"})
    request = service.search.call_args.args[0]
    assert (request.user, request.project, request.agent) == ("default", "all", "agent")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_explicit_search_parameters_remain_compatible():
    service = AsyncMock()
    service.search.return_value = []
    await create_mcp(service).call_tool("memory_search", {
        "query": "evaluation", "user": "alice", "agent": "claude", "project": "test-fixture",
    })
    assert service.search.call_args.args[0].project == "test-fixture"
