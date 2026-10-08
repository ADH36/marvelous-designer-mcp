"""Start the MCP listener inside Marvelous Designer.

Two ways to use this file:

  A) Python Editor — paste this whole file into MD's `Plugins > Python Editor`
     and run it.

  B) Plug-in (recommended for day-to-day use) — register this file via
     `Plugins > Plug-in Manager > +ADD`, then click it under `Plugins > Plug-in`.
     One click instead of opening the editor and pasting.

Either way it starts a listener on 127.0.0.1:7421. Version 0.8 dispatches
Windows GUI messages while idle; native calls remain synchronous and can pause
the UI. This host integration awaits live validation. Stop through shutdown_listener.

Re-running picks up edits to md_listener.py (the module is reloaded), so you do
not need to restart MD after changing it.

Status / errors are appended to ~/md_mcp_listener.log -- useful in Plug-in mode,
where stdout may not be visible anywhere.
"""
import os
import sys
import traceback

_LOG_PATH = os.path.expanduser(r"~\md_mcp_listener.log")


def _log(msg: str) -> None:
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# Locate the md_addon directory (where md_listener.py lives). Registered plugins
# and scripts use their own location; pasted code can use MD_MCP_ADDON_DIR.
try:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _ADDON_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "md_addon"))
except NameError:
    _ADDON_DIR = os.environ.get("MD_MCP_ADDON_DIR", "").strip()

if not _ADDON_DIR or not os.path.isdir(_ADDON_DIR):
    raise RuntimeError(
        "Cannot locate md_addon. Register this launcher as an MD plug-in, or set "
        "MD_MCP_ADDON_DIR to this repository's md_addon directory before starting MD."
    )

if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

_log(f"--- md-mcp listener launch; addon dir = {_ADDON_DIR} ---")
try:
    import importlib
    import md_listener
    import cooperative_listener
    if cooperative_listener._running:
        raise RuntimeError('Listener already running; stop it before restarting')
    importlib.reload(cooperative_listener)
    importlib.reload(md_listener)
    _log("starting serve_forever() -- idle Windows GUI pump enabled unless MD_MCP_UI_PUMP=0; native calls remain synchronous")
    md_listener.serve_forever()
    _log("serve_forever() returned -- listener stopped, GUI released")
except Exception:
    _log("LISTENER LAUNCH FAILED:\n" + traceback.format_exc())
    raise
