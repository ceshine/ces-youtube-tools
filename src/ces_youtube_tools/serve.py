"""Serve a folder of guide pages on localhost."""

import errno
import logging
import functools
from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class ServeError(Exception):
    """Raised when the server cannot be started."""


class _GuideHandler(SimpleHTTPRequestHandler):
    """Serve files, but only for requests addressed to this machine by name or IP.

    A web page on another domain can point its own name at 127.0.0.1 (DNS rebinding) and read local servers. Such
    requests still carry the foreign name in the `Host` header, so they are refused.
    """

    def _host_allowed(self) -> bool:
        server = self.server
        if not isinstance(server, HTTPServer):
            return False
        port = server.server_port
        return self.headers.get("Host") in {f"{HOST}:{port}", f"localhost:{port}"}

    def do_GET(self) -> None:
        if not self._host_allowed():
            self.send_error(403, "Forbidden host")
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self._host_allowed():
            self.send_error(403, "Forbidden host")
            return
        super().do_HEAD()

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.address_string(), format % args)


def make_server(directory: Path, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Create a server for a folder, bound to the loopback address only.

    Args:
        directory (Path): The folder to serve.
        port (int): The port. 0 picks a free one; read it from `server.server_address[1]`.

    Returns:
        ThreadingHTTPServer: The server, listening but not yet serving. Call `serve_forever()`.

    Raises:
        ServeError: If the folder does not exist or the port cannot be used.
    """
    if not directory.is_dir():
        raise ServeError(f"Not a folder: {directory}")
    handler = functools.partial(_GuideHandler, directory=str(directory))
    try:
        return ThreadingHTTPServer((HOST, port), handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise ServeError(f"Port {port} is already in use. Try another with --port.") from exc
        raise ServeError(f"Cannot listen on {HOST}:{port} ({exc})") from exc
