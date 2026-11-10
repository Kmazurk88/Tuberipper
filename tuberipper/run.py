"""Starts the web server. Always the normal port (8000, for the home network); and, when
TUBERIPPER_PUBLIC_PORT is set, a second port serving the same app behind a login (auth.PublicGate) for
use over the internet. Only point a tunnel at the second one."""

import os

import uvicorn

from . import server


def main():
    public_port = os.environ.get("TUBERIPPER_PUBLIC_PORT")
    public = None
    if public_port:
        import threading
        public = uvicorn.Server(uvicorn.Config(server.public_app, host="0.0.0.0", port=int(public_port), log_level="info"))
        # In a thread uvicorn leaves signal handling to the main server, which stops this one below.
        threading.Thread(target=public.run, daemon=True).start()
    try:
        uvicorn.run(server.app, host="0.0.0.0", port=8000, log_level="info")
    finally:
        if public:
            public.should_exit = True


if __name__ == "__main__":
    main()
