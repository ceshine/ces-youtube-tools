"""Discover Firefox profiles and check whether they are signed in to YouTube.

Firefox lists profiles in two places. `profiles.ini` holds standalone profiles, but only one entry per profile group
(whichever group member was used last). The profile manager added in 2025 keeps every group member, with its display
name, in `Profile Groups/*.sqlite`. Profiles are taken from both, and names from the database take precedence.
"""

import shutil
import logging
import sqlite3
import tempfile
import configparser
from pathlib import Path
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

# Locations relative to the home directory: standard, XDG, Snap and Flatpak installs.
FIREFOX_ROOTS = (
    Path(".mozilla/firefox"),
    Path(".config/mozilla/firefox"),
    Path("snap/firefox/common/.mozilla/firefox"),
    Path(".var/app/org.mozilla.firefox/.mozilla/firefox"),
)
YOUTUBE_LOGIN_COOKIES = frozenset({"SAPISID", "LOGIN_INFO"})


@dataclass(frozen=True)
class FirefoxProfile:
    """A Firefox profile on disk.

    Attributes:
        name (str): Display name of the profile.
        path (Path): Absolute path of the profile folder.
    """

    name: str
    path: Path


def _query_copy(db_path: Path, sql: str) -> list[tuple[str, ...]]:
    """Run a query against a copy of a SQLite database that Firefox may be holding open.

    The `-wal` file is copied too, because Firefox writes recent changes there first.

    Args:
        db_path (Path): The database file.
        sql (str): The query to run.

    Returns:
        list[tuple[str, ...]]: The result rows.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / db_path.name
        shutil.copy2(db_path, tmp_db)
        wal = db_path.with_name(db_path.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, tmp_db.with_name(tmp_db.name + "-wal"))
        connection = sqlite3.connect(tmp_db)
        try:
            return connection.execute(sql).fetchall()
        finally:
            connection.close()


def find_firefox_roots(home: Path) -> list[Path]:
    """List the Firefox data folders that exist under a home directory.

    Args:
        home (Path): The home directory to search.

    Returns:
        list[Path]: Existing Firefox data folders.
    """
    return [home / root for root in FIREFOX_ROOTS if (home / root).is_dir()]


def _resolve_profile_path(root: Path, path: str) -> Path:
    """Turn a profile path from Firefox's records into an absolute path.

    Args:
        root (Path): The Firefox data folder.
        path (str): The recorded path, relative to `root` or absolute.

    Returns:
        Path: The absolute profile folder path.
    """
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _read_ini_profiles(root: Path) -> dict[Path, str]:
    """Read profile folders and names from `profiles.ini`.

    Args:
        root (Path): The Firefox data folder.

    Returns:
        dict[Path, str]: Profile folder to name.
    """
    ini_path = root / "profiles.ini"
    if not ini_path.exists():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    _ = parser.read(ini_path, encoding="utf-8")
    profiles: dict[Path, str] = {}
    for section in parser.sections():
        if section.startswith("Profile") and parser.has_option(section, "Path"):
            folder = _resolve_profile_path(root, parser.get(section, "Path"))
            profiles[folder] = parser.get(section, "Name", fallback=folder.name)
    return profiles


def _read_group_names(root: Path) -> dict[Path, str]:
    """Read profile display names from Firefox's profile manager databases.

    Args:
        root (Path): The Firefox data folder.

    Returns:
        dict[Path, str]: Profile folder to display name.
    """
    names: dict[Path, str] = {}
    for db_path in sorted((root / "Profile Groups").glob("*.sqlite")):
        try:
            rows = _query_copy(db_path, "SELECT path, name FROM Profiles")
        except sqlite3.Error as exc:
            LOGGER.warning("Could not read Firefox profile names from %s: %s", db_path, exc)
            continue
        for path, name in rows:
            names[_resolve_profile_path(root, path)] = name
    return names


def discover_profiles(root: Path) -> list[FirefoxProfile]:
    """List the profiles in one Firefox data folder.

    Args:
        root (Path): The Firefox data folder.

    Returns:
        list[FirefoxProfile]: Profiles whose folders exist: `profiles.ini` entries first, then profiles found only
            in the profile manager database.
    """
    names = _read_ini_profiles(root)
    names.update(_read_group_names(root))
    return [FirefoxProfile(name=name, path=path) for path, name in names.items() if path.is_dir()]


def discover_all_profiles(home: Path) -> list[FirefoxProfile]:
    """List the profiles of every Firefox install under a home directory.

    Args:
        home (Path): The home directory to search.

    Returns:
        list[FirefoxProfile]: All profiles found.
    """
    return [profile for root in find_firefox_roots(home) for profile in discover_profiles(root)]


def is_signed_in(profile_path: Path) -> bool:
    """Check whether a profile holds YouTube sign-in cookies.

    Only cookie names are read, never their values. The cookies may still have expired on YouTube's side; use a live
    request to be sure.

    Args:
        profile_path (Path): The profile folder.

    Returns:
        bool: True if the sign-in cookies are present.
    """
    cookies_db = profile_path / "cookies.sqlite"
    if not cookies_db.exists():
        return False
    try:
        rows = _query_copy(cookies_db, "SELECT name FROM moz_cookies WHERE host = '.youtube.com'")
    except sqlite3.Error as exc:
        LOGGER.warning("Could not read cookies from %s: %s", cookies_db, exc)
        return False
    return YOUTUBE_LOGIN_COOKIES <= {name for (name,) in rows}


def cookies_from_browser_spec(profile_path: Path) -> str:
    """Build the value of yt-dlp's `--cookies-from-browser` option for a profile.

    Args:
        profile_path (Path): The profile folder.

    Returns:
        str: The option value, e.g. `firefox:/home/user/.mozilla/firefox/abc.default`.
    """
    return f"firefox:{profile_path}"
