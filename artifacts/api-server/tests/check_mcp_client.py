"""Official Python MCP SDK over real HTTP, with disposable operator-created runs."""
import asyncio
import json
import httpx
from anyio import BrokenResourceError, ClosedResourceError
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.client.streamable_http import streamable_http_client
from check_sandbox_api import BASE, call, client


class DropCommittedResponse(httpx.AsyncHTTPTransport):
    """Lose one successful action response after the real server commits it."""

    dropped = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        if request.method == "POST":
            message = json.loads(request.content)
            if message.get("method") == "tools/call" and not self.dropped:
                assert response.status_code == 200
                await response.aread()
                await response.aclose()
                self.dropped = True
                raise httpx.ReadError("Injected lost action response", request=request)
        return response


def contains_error(error: BaseException, kind: type, status: int | None = None) -> bool:
    if isinstance(error, kind):
        return status is None or error.response.status_code == status
    return any(contains_error(child, kind, status) for child in getattr(error, "exceptions", ()))


async def check_recovery() -> None:
    owner = client()
    assert call(owner, "/owner", "POST")[0] == 200
    status, created = call(owner, "/runs", "POST", {"scenario": "non_delivery"})
    assert status == 200
    run, token = created["run"]["id"], created["agent_token"]
    url = BASE + f"/api/sandbox/agent/{run}/mcp"
    headers = {"Authorization": "Bearer " + token}
    transport = DropCommittedResponse()
    try:
        try:
            async with httpx.AsyncClient(headers=headers, transport=transport) as http:
                async with streamable_http_client(url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.call_tool("inspect_offer", {"idempotency_key": "lost-response"})
            raise AssertionError("Expected transport failure")
        except Exception as exc:
            assert contains_error(exc, httpx.ReadError), type(exc).__name__
        assert transport.dropped
        before = call(owner, f"/runs/{run}")[1]
        assert len(before["events"]) == 1
        async with httpx.AsyncClient(headers=headers) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    replay = await session.call_tool("inspect_offer", {"idempotency_key": "lost-response"})
                    assert not replay.isError
                    assert json.loads(replay.content[0].text)["replayed"] is True
        after = call(owner, f"/runs/{run}")[1]
        assert after["events"] == before["events"]
        assert after["state"] == before["state"] and after["consent"] is False
        # Revoke during an initialized connection; discovery must fail immediately.
        discovery_statuses: list[int] = []

        async def record_discovery(response: httpx.Response) -> None:
            if response.request.method == "POST":
                if json.loads(response.request.content).get("method") == "tools/list":
                    discovery_statuses.append(response.status_code)

        try:
            async with httpx.AsyncClient(
                headers=headers, event_hooks={"response": [record_discovery]}
            ) as http:
                async with streamable_http_client(url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        assert len((await session.list_tools()).tools) == 9
                        assert call(owner, f"/runs/{run}/revoke", "POST")[0] == 200
                        await session.list_tools()
            raise AssertionError("Revoked discovery unexpectedly succeeded")
        except Exception as exc:
            assert any(contains_error(exc, kind) for kind in (
                httpx.HTTPStatusError, McpError, BrokenResourceError, ClosedResourceError,
            )), type(exc).__name__
        assert discovery_statuses == [200, 401]
        assert call(owner, f"/runs/{run}")[1]["events"] == before["events"]
        print("PASS: MCP SDK lost-response recovery without duplicate action; live-session revocation returns 401.")
    finally:
        assert call(owner, f"/runs/{run}", "DELETE")[0] == 200


async def main():
    owner = client()
    assert call(owner, "/owner", "POST")[0] == 200
    status, created = call(owner, "/runs", "POST", {"scenario": "non_delivery"})
    assert status == 200
    run = created["run"]["id"]
    token = created["agent_token"]
    url = BASE + f"/api/sandbox/agent/{run}/mcp"
    try:
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.capabilities.tools is not None
                    tools = await session.list_tools()
                    assert len(tools.tools) == 9
                    result = await session.call_tool("inspect_offer", {"idempotency_key": "sdk-inspect"})
                    assert not result.isError
                    replay = await session.call_tool("inspect_offer", {"idempotency_key": "sdk-inspect"})
                    assert json.loads(replay.content[0].text)["replayed"]
                    # An HTTP retry shares the exact action ledger.
                    status, replay = call(client(), f"/agent/{run}/actions", "POST",
                        {"tool": "inspect_offer"}, token=token, key="sdk-inspect", browser_headers=False)
                    assert status == 200 and replay["replayed"]
                    finished = await session.call_tool("finish", {"idempotency_key": "sdk-finish", "claim": "unresolved"})
                    assert not finished.isError
        report = call(owner, f"/runs/{run}")[1]
        assert len(report["events"]) == 2 and report["consent"] is False
        print("PASS: official MCP SDK initialize, discovery, call, replay, cross-HTTP replay, finish; consent unchanged.")
    finally:
        assert call(owner, f"/runs/{run}", "DELETE")[0] == 200


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(check_recovery())
