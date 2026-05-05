from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
import os

security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
    allowed_hosts=["*"],
)

mcp = FastMCP("phone-agent", transport_security=security)


@mcp.tool()
def hello() -> str:
    """Say hello to confirm the MCP server is alive."""
    return "Hello from phone-agent MCP server!"


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the input text. Useful for testing connectivity."""
    return f"Echo: {text}"


if __name__ == "__main__":
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http")
