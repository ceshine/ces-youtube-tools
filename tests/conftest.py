import sqlite3
from pathlib import Path

import pytest

PROFILES_INI = """\
[General]
StartWithLastProfile=1
Version=2

[Profile0]
Name=default-release
IsRelative=1
Path=ghi.default-release
StoreID=store

[Profile1]
Name=default
IsRelative=1
Path=def.default

[Profile2]
Name=deleted
IsRelative=1
Path=missing.folder
"""


def make_cookies_db(profile_dir: Path, names: list[str], host: str = ".youtube.com") -> None:
    """Create a minimal Firefox cookies database holding cookies with the given names."""
    connection = sqlite3.connect(profile_dir / "cookies.sqlite")
    connection.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, host TEXT, name TEXT, value TEXT)")
    connection.executemany(
        "INSERT INTO moz_cookies (host, name, value) VALUES (?, ?, 'secret')", [(host, name) for name in names]
    )
    connection.commit()
    connection.close()


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """A home directory with one standard Firefox install holding three profiles.

    Like real Firefox, `profiles.ini` lists only the last-used member of the profile group, so the signed-in
    "YouTube Turk" profile appears only in the profile manager database.
    """
    root = tmp_path / ".mozilla" / "firefox"
    original = root / "ghi.default-release"
    signed_out = root / "def.default"
    signed_in = root / "abc.Profile 1"
    for folder in (original, signed_out, signed_in):
        folder.mkdir(parents=True)
    (root / "profiles.ini").write_text(PROFILES_INI, encoding="utf-8")

    groups = root / "Profile Groups"
    groups.mkdir()
    connection = sqlite3.connect(groups / "store.sqlite")
    connection.execute("CREATE TABLE Profiles (id INTEGER PRIMARY KEY, path TEXT, name TEXT)")
    connection.executemany(
        "INSERT INTO Profiles (path, name) VALUES (?, ?)",
        [("ghi.default-release", "Original profile"), ("abc.Profile 1", "YouTube Turk"), ("gone.Profile 2", "Gone")],
    )
    connection.commit()
    connection.close()

    make_cookies_db(signed_in, ["SAPISID", "LOGIN_INFO", "PREF"])
    make_cookies_db(signed_out, ["PREF", "VISITOR_INFO1_LIVE"])
    return tmp_path
