"""Cross-platform MCP acceptance check. Read-only unless --session is requested."""
import argparse
import json
import socket
import urllib.request
from uuid import uuid4


def check(url, agent, session_test=False):
    def rpc(method, params, request_id=1):
        body = json.dumps({"jsonrpc": "2.0", "id": request_id,
                           "method": method, "params": params}).encode()
        request = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    init = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "memory-device-check", "version": "1"}})
    assert init["serverInfo"]["name"] == "agent-memory"
    assert "project='all'" in init.get("instructions", "")
    # Gateway is stateless: no session header is necessary. Complete the handshake.
    notification = urllib.request.Request(url, data=json.dumps({
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }).encode(), headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(notification, timeout=30) as response:
        assert response.status in (200, 202, 204)
    listed = rpc("tools/list", {})
    names = {tool["name"] for tool in listed["tools"]}
    assert {"memory_search", "memory_get", "memory_remember", "session_put", "session_get"} <= names
    resource = rpc("resources/read", {"uri": "memory://usage"})
    assert any("Deletion is prohibited" in item.get("text", "") for item in resource["contents"])
    found = rpc("tools/call", {"name": "memory_search", "arguments": {
        "query": "Agent Memory 本番 切替", "agent": agent, "limit": 3,
    }})
    assert not found.get("isError")
    if session_test:
        sid = f"check-{agent}-{socket.gethostname()}-{uuid4().hex}"
        value = {"connection_check": "ok", "agent": agent}
        result = rpc("tools/call", {"name": "session_put", "arguments": {
            "session_id": sid, "key": "check", "value": value, "ttl_seconds": 60,
        }})
        assert not result.get("isError")
        result = rpc("tools/call", {"name": "session_get", "arguments": {"session_id": sid, "key": "check"}})
        assert not result.get("isError")
        parsed = [json.loads(c["text"]) for c in result.get("content", []) if c.get("type") == "text"]
        assert value in parsed, "session roundtrip mismatch"
    return {"server": init["serverInfo"]["name"], "tools": len(names),
            "instructions": True, "resource": True, "search_call": True,
            "session_roundtrip": session_test, "agent": agent}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8377/mcp/")
    parser.add_argument("--agent", default="device-check")
    parser.add_argument("--session", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check(args.url, args.agent, args.session), ensure_ascii=False))
