import os

MD_HOST = os.environ.get("MD_MCP_HOST", "127.0.0.1")
MD_PORT = int(os.environ.get("MD_MCP_PORT", "7421"))
# Generous default: most calls return in <1s, but heavy ops (export of a large
# .zprj, simulation, rendering) can take much longer. Raise MD_MCP_TIMEOUT for
# very long renders. Native calls can pause the GUI; v0.8 pumps Windows messages while idle.
MD_TIMEOUT = float(os.environ.get("MD_MCP_TIMEOUT", "120.0"))
MD_MAX_RESPONSE_BYTES = int(os.environ.get("MD_MCP_MAX_RESPONSE_BYTES", str(16 * 1024 * 1024)))
if MD_MAX_RESPONSE_BYTES < 1:
    raise ValueError("MD_MCP_MAX_RESPONSE_BYTES must be a positive integer")
