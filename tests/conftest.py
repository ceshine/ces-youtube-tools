import sqlite3
from pathlib import Path

import pytest

from ces_youtube_tools.video import VideoInfo
from ces_youtube_tools.front_matter import GuideMetadata, read_front_matter, render_front_matter

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


GUIDE_BODY = "# Watching Guide: The heading\n\n[0:05](https://www.youtube.com/watch?v={video_id}&t=5s): Hello.\n"


def write_guide(folder: Path, name: str, video_id: str, **fields: object) -> Path:
    """Write a guide with front matter; `fields` are `GuideMetadata` fields besides the video ID."""
    metadata = GuideMetadata.model_validate({"video_id": video_id, **fields})
    path = folder / name
    _ = path.write_text(render_front_matter(metadata) + GUIDE_BODY.format(video_id=video_id), encoding="utf-8")
    return path


def write_sample_guides(parent: Path) -> Path:
    """Write five guides into `parent / "guides"`: two public, one members-only, one unknown, one without front matter.

    Args:
        parent (Path): The folder to create the `guides` folder in.

    Returns:
        Path: The `guides` folder.
    """
    folder = parent / "guides"
    folder.mkdir()
    _ = write_guide(folder, "old.md", "aaaaaaaaaaa", title="Old", upload_date="2026-10-01", members_only=False)
    _ = write_guide(folder, "new.md", "bbbbbbbbbbb", title="New", upload_date="2026-10-08", members_only=False)
    _ = write_guide(folder, "members.md", "ccccccccccc", title="Members", upload_date="2026-10-09", members_only=True)
    _ = write_guide(folder, "unknown.md", "ddddddddddd", title="Unknown", members_only=None)
    _ = (folder / "plain.md").write_text(GUIDE_BODY.format(video_id="eeeeeeeeeee"), encoding="utf-8")
    return folder


# The backfill samples, shared by tests/test_backfill.py and tests/cli/test_backfill_cmd.py. GUIDE is a fixed guide
# for VIDEO, with CRLF line endings and no front matter; unlike GUIDE_BODY, it is not a template.

GUIDE = "# Watching Guide: A talk\r\n\r\n[0:05](https://www.youtube.com/watch?v=abcdefghijk&t=5s): Hello.\r\n"
OTHER_GUIDE = "# Another talk\n\n[0:01](https://www.youtube.com/watch?v=zzzzzzzzzzz&t=1s): Hi.\n"
# 2026-10-08T16:30:00Z, which is 2026-10-09 in Asia/Taipei.
VIDEO = VideoInfo(
    id="abcdefghijk", title="A talk", channel="A channel", upload_date="20261008", duration=60, timestamp=1791477000
)
NEW_NAME = "Watching Guide - 20261009 - A talk.md"


class FakeLoader:
    def __init__(self, failing: dict[str, Exception] | None = None) -> None:
        self.failing: dict[str, Exception] = failing or {}
        self.calls: list[str] = []

    def __call__(self, video_id: str) -> VideoInfo:
        self.calls.append(video_id)
        if video_id in self.failing:
            raise self.failing[video_id]
        if video_id == VIDEO.id:
            return VIDEO
        return VIDEO.model_copy(update={"id": video_id, "title": f"Talk {video_id}", "timestamp": None})


def front_matter(path: Path) -> dict[object, object] | None:
    return read_front_matter(path.read_bytes().decode("utf-8")).data


def write_backfill_guides(parent: Path) -> Path:
    """Write `GUIDE`, a guide without front matter, as `new.md` in `parent / "guides"`.

    Args:
        parent (Path): The folder to create the `guides` folder in.

    Returns:
        Path: The `guides` folder.
    """
    folder = parent / "guides"
    folder.mkdir()
    _ = (folder / "new.md").write_bytes(GUIDE.encode("utf-8"))
    return folder
