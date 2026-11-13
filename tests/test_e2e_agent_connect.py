"""End to end: the REAL relay server (Node) + the REAL device client (Python)
+ an agent that speaks MCP over HTTP. Checks the contract in docs/PACKS.md
across the two languages. Skipped when Node or the server's packages are missing.
"""
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import time

import pytest
import requests

from leeloo_client import LeelooClient
from leeloo_packs import validate_pack

SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leeloo_server")
PACK = {"pack": 1, "id": "e2e-blue", "name": "E2E Blue",
        "theme": {"colors": {"bg": "#13294B"}, "header": "GO HEELS"},
        "panels": [{"slot": "weather", "title": "hello", "rows": [{"left": "from the agent"}]}]}

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not os.path.isdir(os.path.join(SERVER_DIR, "node_modules")),
    reason="node or leeloo_server/node_modules not available")


@pytest.fixture
def relay(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PORT=str(port), LEELOO_DATA_FILE=str(tmp_path / "state.json"),
               PUBLIC_BASE_URL=base, NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    proc = subprocess.Popen(["node", "server.js"], cwd=SERVER_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if requests.get(base + "/health", timeout=1, proxies={"http": None, "https": None}).ok:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        else:
            pytest.fail("relay did not start")
        yield base, port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def http(method, url, **kw):
    return requests.request(method, url, timeout=10, proxies={"http": None, "https": None}, **kw)


def mcp(base, token, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    r = http("POST", f"{base}/mcp/{token}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def tool(base, token, name, args=None):
    reply = mcp(base, token, "tools/call", {"name": name, "arguments": args or {}})
    result = reply["result"]
    return result.get("isError", False), result["content"][0]["text"]


def test_connect_an_agent_install_and_share_a_pack(relay, tmp_path):
    base, port = relay
    assert validate_pack(PACK) == []

    async def scenario():
        seen = {"nat": [], "amy": []}
        nat = LeelooClient(relay_url=f"ws://127.0.0.1:{port}/ws", config_path=str(tmp_path / "nat.json"))
        amy = LeelooClient(relay_url=f"ws://127.0.0.1:{port}/ws", config_path=str(tmp_path / "amy.json"))
        nat.on_agent_connect = lambda code, url, exp: seen["nat"].append(("connect", code, url, exp))
        nat.on_pack_install = lambda pack: seen["nat"].append(("install", pack))
        nat.on_pack_offer = lambda who, pack: seen["nat"].append(("offer", who, pack))
        nat.on_pack_reset = lambda: seen["nat"].append(("reset",))
        nat.on_message = lambda who, text: seen["nat"].append(("text", who, text))
        amy.on_pack_offer = lambda who, pack: seen["amy"].append(("offer", who, pack))
        amy.on_pack_install = lambda pack: seen["amy"].append(("install", pack))
        amy.on_message = lambda who, text: seen["amy"].append(("text", who, text))

        assert await nat.connect()
        crew = await nat.create_crew("Nat")
        assert crew
        assert await amy.connect() and await amy.join_crew(crew, "Amy")
        tasks = [asyncio.ensure_future(c.listen()) for c in (nat, amy)]
        loop = asyncio.get_running_loop()
        call = lambda fn, *a, **k: loop.run_in_executor(None, lambda: fn(*a, **k))

        async def wait_for(who, kind, timeout=5.0):
            end = time.time() + timeout
            while time.time() < end:
                for item in seen[who]:
                    if item[0] == kind:
                        return item
                await asyncio.sleep(0.02)
            raise AssertionError(f"{who} did not get {kind}: {seen[who]}")

        try:
            # 1. "connect my agent": the device gets a code and a page URL
            assert await nat.request_agent_connect()
            _, code, url, expires = await wait_for("nat", "connect")
            assert url == f"{base}/connect/{code}" and expires > 0

            # 2. The user opens the page and creates the connector link
            assert (await call(http, "GET", url)).status_code == 200
            page = await call(http, "POST", url)
            assert page.status_code == 200
            token = re.search(r"/mcp/([0-9a-f]{64})", page.text).group(1)
            assert (await call(http, "POST", url)).status_code != 200          # the code works one time only

            # 3. The agent talks MCP
            init = await call(mcp, base, token, "initialize",
                              {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
            assert init["result"]["serverInfo"]["name"] == "leeloo"
            names = {t["name"] for t in (await call(mcp, base, token, "tools/list"))["result"]["tools"]}
            assert names == {"get_status", "get_pack_guide", "validate_pack", "install_pack",
                             "share_pack", "reset_pack", "send_message"}
            err, text = await call(tool, base, token, "get_status")
            assert not err and "Nat" in text and "Amy" in text

            # 4. A bad pack is refused with a useful message; nothing reaches the device
            err, text = await call(tool, base, token, "install_pack", {"pack": dict(PACK, run="rm -rf /")})
            assert err and "run" in text
            assert not [i for i in seen["nat"] if i[0] == "install"]

            # 5. A good pack is installed on the owner's device only
            err, text = await call(tool, base, token, "install_pack", {"pack": PACK})
            assert not err, text
            assert (await wait_for("nat", "install"))[1] == PACK
            assert await nat.send_pack_active(PACK)

            # 6. Sharing offers it to the friend, not to the owner; the friend is not forced
            err, text = await call(tool, base, token, "share_pack", {"pack": PACK})
            assert not err, text
            offer = await wait_for("amy", "offer")
            assert offer[1] == "Nat" and offer[2] == PACK
            assert not [i for i in seen["amy"] if i[0] == "install"]
            assert not [i for i in seen["nat"] if i[0] == "offer"]

            # 7. The agent sends a message in the owner's name
            err, _ = await call(tool, base, token, "send_message", {"text": "new look is up"})
            assert not err
            assert (await wait_for("amy", "text"))[1:] == ("Nat", "new look is up")

            # 8. Reset, then "disconnect my agent" kills the link
            err, _ = await call(tool, base, token, "reset_pack")
            assert not err
            await wait_for("nat", "reset")
            assert await nat.send_agent_disconnect()
            for _ in range(50):
                if (await call(http, "POST", f"{base}/mcp/{token}", json={"jsonrpc": "2.0", "id": 9, "method": "ping"})).status_code == 401:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("the connector link still works after disconnect")
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await nat.disconnect()
            await amy.disconnect()

    asyncio.run(scenario())
