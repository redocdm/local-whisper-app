"""
Entry point maintained for backward compatibility.

The websocket server implementation now lives in `python/ws_server.py`.
"""

from ws_server import main  # noqa: F401


if __name__ == "__main__":
    # Keep running the new server implementation for existing entrypoints.
    import asyncio

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        from util.logging_utils import log

        log("Shutting down.")
        sys.exit(0)

