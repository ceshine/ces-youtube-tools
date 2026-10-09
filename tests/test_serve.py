import http.client
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from youtube_tools import cli
from youtube_tools.cli import APP
from youtube_tools.serve import ServeError, make_server
from youtube_tools.config import AppConfig, GuideConfig, save_config

RUNNER = CliRunner()


@pytest.fixture
def pages(tmp_path: Path) -> Path:
    folder = tmp_path / "pages"
    folder.mkdir()
    (folder / "a.html").write_text("<p>guide a</p>", encoding="utf-8")
    return folder


@pytest.fixture
def running(pages: Path) -> Iterator[ThreadingHTTPServer]:
    server = make_server(pages, 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def get(server: ThreadingHTTPServer, path: str, host: str | None = None) -> tuple[int, str]:
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Host": host} if host is not None else {}
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    body = response.read().decode("utf-8")
    connection.close()
    return response.status, body


def test_serves_files_from_the_folder(running: ThreadingHTTPServer) -> None:
    assert get(running, "/a.html") == (200, "<p>guide a</p>")
    assert get(running, "/missing.html")[0] == 404


def test_listing_is_the_index(running: ThreadingHTTPServer) -> None:
    status, body = get(running, "/")
    assert status == 200 and "a.html" in body


def test_binds_to_loopback_only(running: ThreadingHTTPServer) -> None:
    assert running.server_address[0] == "127.0.0.1"


def test_does_not_serve_outside_the_folder(running: ThreadingHTTPServer, pages: Path) -> None:
    (pages.parent / "secret.txt").write_text("secret", encoding="utf-8")
    status, body = get(running, "/../secret.txt")
    assert "secret" not in body or status != 200


def test_allows_localhost_host_header(running: ThreadingHTTPServer) -> None:
    port = running.server_address[1]
    assert get(running, "/a.html", host=f"localhost:{port}")[0] == 200


def test_refuses_foreign_host_header(running: ThreadingHTTPServer) -> None:
    port = running.server_address[1]
    status, body = get(running, "/a.html", host=f"evil.example:{port}")
    assert status == 403 and "guide a" not in body
    assert get(running, "/a.html", host="localhost")[0] == 403


def test_port_in_use(pages: Path) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        with pytest.raises(ServeError, match="--port"):
            make_server(pages, blocker.getsockname()[1])


def test_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="Not a folder"):
        make_server(tmp_path / "nope", 0)


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: urls.append(url) or True)

    # Return from serve_forever at once, as if Ctrl+C was pressed.
    def interrupt(self: ThreadingHTTPServer, poll_interval: float = 0.5) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", interrupt)
    return urls


def test_serve_command_opens_the_browser(pages: Path, opened: list[str]) -> None:
    result = RUNNER.invoke(APP, ["serve", str(pages), "--port", "0"])
    assert result.exit_code == 0
    assert len(opened) == 1 and opened[0].startswith("http://localhost:")
    assert "Stopped." in result.output


def test_serve_command_no_open(pages: Path, opened: list[str]) -> None:
    result = RUNNER.invoke(APP, ["serve", str(pages), "--port", "0", "--no-open"])
    assert result.exit_code == 0 and opened == []


def test_serve_command_defaults_to_output_dir(pages: Path, tmp_path: Path, opened: list[str]) -> None:
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(guide=GuideConfig(output_dir=pages)), config_path)
    result = RUNNER.invoke(APP, ["serve", "--port", "0", "--no-open", "--config", str(config_path)])
    assert result.exit_code == 0
    assert str(pages) in result.output


def test_serve_command_needs_a_folder(tmp_path: Path, opened: list[str]) -> None:
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["serve", "--no-open", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "guide.output_dir" in result.output
    assert opened == []


def test_serve_command_port_in_use(pages: Path, opened: list[str]) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        result = RUNNER.invoke(APP, ["serve", str(pages), "--port", str(blocker.getsockname()[1])])
    assert result.exit_code == 1
    assert "--port" in result.output
