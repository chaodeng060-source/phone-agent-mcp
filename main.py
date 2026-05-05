from fastapi import FastAPI, Request, HTTPException
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from contextlib import asynccontextmanager
from collections import deque
import base64, time, os, asyncio

# Shared in-memory state
command_queue = deque()
latest_screenshot = {"data": None, "ts": 0, "req_id": None}

# MCP config
security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
    allowed_hosts=["*"],
)
mcp = FastMCP("phone-agent", transport_security=security)

# ---- MCP Tools ----
@mcp.tool()
def hello() -> str:
    """Say hello to confirm the MCP server is alive."""
    return "Hello from phone-agent MCP server!"

@mcp.tool()
async def take_screenshot() -> Image:
    """Trigger the phone to take a screenshot and return it."""
    req_id = str(time.time())
    command_queue.append({"type": "screenshot", "req_id": req_id})
    for _ in range(150):
        if latest_screenshot["req_id"] == req_id and latest_screenshot["data"]:
            return Image(data=base64.b64decode(latest_screenshot["data"]), format="png")
        await asyncio.sleep(0.1)
    raise Exception("Screenshot timeout after 15s. Check that phone is online.")

@mcp.tool()
def tap(x: int, y: int) -> str:
    """Queue a tap at (x, y) on the phone."""
    command_queue.append({"type": "tap", "x": x, "y": y})
    return f"Queued tap at ({x}, {y})"

@mcp.tool()
def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
    """Queue a swipe from (x1,y1) to (x2,y2)."""
    command_queue.append({
        "type": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration_ms
    })
    return f"Queued swipe ({x1},{y1}) -> ({x2},{y2})"

@mcp.tool()
def input_text(text: str) -> str:
    """Queue typing text (must focus an input field first)."""
    command_queue.append({"type": "input", "text": text})
    return f"Queued input: {text[:30]}"

@mcp.tool()
def launch_app(package_name: str) -> str:
    """Queue launching an app by package name (e.g. com.android.chrome)."""
    command_queue.append({"type": "launch", "package": package_name})
    return f"Queued launch: {package_name}"

@mcp.tool()
def home() -> str:
    """Queue a home button press."""
    command_queue.append({"type": "home"})
    return "Queued home"

@mcp.tool()
def back() -> str:
    """Queue a back button press."""
    command_queue.append({"type": "back"})
    return "Queued back"

# ---- Phone-facing endpoints ----
PHONE_TOKEN = os.environ.get("PHONE_TOKEN", "change-me")

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)

@app.get("/api/next_command")
async def next_command(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    if command_queue:
        return command_queue.popleft()
    return {"type": "noop"}

@app.post("/api/upload_screenshot")
async def upload_screenshot(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    latest_screenshot["data"] = body.get("data")
    latest_screenshot["ts"] = time.time()
    latest_screenshot["req_id"] = body.get("req_id")
    return {"ok": True}

app.mount("/", mcp.streamable_http_app())

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
