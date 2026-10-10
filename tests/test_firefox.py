from pathlib import Path

from ces_youtube_tools.firefox import (
    is_signed_in,
    FirefoxProfile,
    discover_profiles,
    find_firefox_roots,
    discover_all_profiles,
    cookies_from_browser_spec,
)

from .conftest import make_cookies_db


def test_find_firefox_roots(fake_home: Path) -> None:
    assert find_firefox_roots(fake_home) == [fake_home / ".mozilla" / "firefox"]


def test_find_firefox_roots_none(tmp_path: Path) -> None:
    assert find_firefox_roots(tmp_path) == []


def test_discover_profiles_merges_ini_and_group_database(fake_home: Path) -> None:
    root = fake_home / ".mozilla" / "firefox"
    assert discover_profiles(root) == [
        FirefoxProfile(name="Original profile", path=root / "ghi.default-release"),
        FirefoxProfile(name="default", path=root / "def.default"),
        FirefoxProfile(name="YouTube Turk", path=root / "abc.Profile 1"),
    ]


def test_discover_profiles_without_group_database(fake_home: Path) -> None:
    root = fake_home / ".mozilla" / "firefox"
    for path in (root / "Profile Groups").iterdir():
        path.unlink()
    assert [profile.name for profile in discover_profiles(root)] == ["default-release", "default"]


def test_discover_all_profiles(fake_home: Path) -> None:
    assert [profile.name for profile in discover_all_profiles(fake_home)] == [
        "Original profile",
        "default",
        "YouTube Turk",
    ]


def test_is_signed_in(fake_home: Path) -> None:
    root = fake_home / ".mozilla" / "firefox"
    assert is_signed_in(root / "abc.Profile 1")
    assert not is_signed_in(root / "def.default")


def test_is_signed_in_ignores_other_hosts(tmp_path: Path) -> None:
    make_cookies_db(tmp_path, ["SAPISID", "LOGIN_INFO"], host=".google.com")
    assert not is_signed_in(tmp_path)


def test_is_signed_in_without_cookie_database(tmp_path: Path) -> None:
    assert not is_signed_in(tmp_path)


def test_cookies_from_browser_spec() -> None:
    assert cookies_from_browser_spec(Path("/p/abc.Profile 1")) == "firefox:/p/abc.Profile 1"
