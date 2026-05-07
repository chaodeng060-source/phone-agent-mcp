from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from contextlib import asynccontextmanager
from collections import deque
from PIL import Image as PILImage
from io import BytesIO
import base64, time, os, asyncio

# ---- Compression config ----
SCREENSHOT_MAX_WIDTH = 600       # 缩到600宽,vivo原图1216,差不多减半
SCREENSHOT_JPEG_QUALITY = 70     # JPEG质量,70足够看清


def compress_screenshot(raw_bytes: bytes) -> bytes:
    """把截图缩到指定宽度,转JPEG."""
    img = PILImage.open(BytesIO(raw_bytes))
    # JPEG不支持透明,有alpha通道先转RGB
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    # 按比例缩放,只在原图比目标宽时才缩
    if img.width > SCREENSHOT_MAX_WIDTH:
        ratio = SCREENSHOT_MAX_WIDTH / img.width
        new_h = int(img.height * ratio)
        img = img.resize((SCREENSHOT_MAX_WIDTH, new_h), PILImage.LANCZOS)
    out = BytesIO()
    img.save(out, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY, optimize=True)
    return out.getvalue()


# ---- Shared in-memory state ----
command_queue = deque()
latest_screenshot = {"data": None, "ts": 0, "req_id": None}
screenshot_event = asyncio.Event()   # 事件驱动,代替原来的0.1s轮询


# ---- MCP config ----
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
    # 重置,确保等的是新数据
    latest_screenshot["data"] = None
    latest_screenshot["req_id"] = None
    screenshot_event.clear()
    command_queue.append({"type": "screenshot", "req_id": req_id})
    try:
        await asyncio.wait_for(screenshot_event.wait(), timeout=60)
    except asyncio.TimeoutError:
        raise Exception("Screenshot timeout after 60s. Check that phone is online.")
    if not latest_screenshot["data"]:
        raise Exception("Screenshot event fired but no data. Server bug.")
    return Image(data=base64.b64decode(latest_screenshot["data"]), format="jpeg")


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
    command_queue.append({"type": "input_text", "text": text})
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
    raw_b64 = body.get("data") or ""
    if raw_b64:
        raw = base64.b64decode(raw_b64)
        compressed = compress_screenshot(raw)
        latest_screenshot["data"] = base64.b64encode(compressed).decode()
    else:
        latest_screenshot["data"] = None
    latest_screenshot["ts"] = time.time()
    latest_screenshot["req_id"] = body.get("req_id")
    screenshot_event.set()
    return {"ok": True}


@app.post("/api/upload_screenshot_file")
async def upload_screenshot_file(
    request: Request,
    file: UploadFile = File(...),
    req_id: str = Form(...)
):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    contents = await file.read()
    compressed = compress_screenshot(contents)
    latest_screenshot["data"] = base64.b64encode(compressed).decode()
    latest_screenshot["ts"] = time.time()
    latest_screenshot["req_id"] = req_id
    screenshot_event.set()
    return {"ok": True}


@app.post("/api/upload_screenshot_raw")
async def upload_screenshot_raw(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(401, "Unauthorized")
    req_id = request.headers.get("X-Req-Id")
    if not req_id:
        raise HTTPException(400, "Missing X-Req-Id header")
    contents = await request.body()
    compressed = compress_screenshot(contents)
    latest_screenshot["data"] = base64.b64encode(compressed).decode()
    latest_screenshot["ts"] = time.time()
    latest_screenshot["req_id"] = req_id
    screenshot_event.set()
    return {"ok": True}


app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
