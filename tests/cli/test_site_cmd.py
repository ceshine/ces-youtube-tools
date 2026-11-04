from pathlib import Path

import pytest
from typer.testing import CliRunner

from ces_youtube_tools.cli import APP
from ces_youtube_tools.config import AppConfig, SiteConfig, GuideConfig, save_config
from ces_youtube_tools.static_site import SITE_MARKER

from ..conftest import write_guide, write_sample_guides

RUNNER = CliRunner()


@pytest.fixture
def guides(tmp_path: Path) -> Path:
    return write_sample_guides(tmp_path)


@pytest.fixture
def config_path(guides: Path, tmp_path: Path) -> Path:
    path = tmp_path / "config" / "config.toml"
    config = AppConfig(guide=GuideConfig(output_dir=guides), site=SiteConfig(output_dir=tmp_path / "configured-site"))
    save_config(config, path)
    return path


def test_site_command_uses_the_configured_folders(config_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "configured-site"
    result = RUNNER.invoke(APP, ["site", "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert f"Wrote 2 guide pages and the index to {out}." in result.output
    assert "Left out 3 members-only guides or guides whose availability is unknown" in result.output
    assert f"npx wrangler pages deploy {out}" in result.output
    assert "Cloudflare Access" not in result.output
    assert (out / "index.html").is_file()


def test_site_command_out_option_beats_config(config_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "from-option"
    result = RUNNER.invoke(APP, ["site", "-o", str(out), "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file()
    assert not (tmp_path / "configured-site").exists()


def test_site_command_warns_when_members_only_guides_are_included(
    config_path: Path, guides: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    args = ["site", str(guides), "-o", str(out), "--include-members-only", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert "Wrote 5 guide pages" in result.output
    assert "Deploy this build only to a project behind Cloudflare Access." in result.output
    assert "Left out" not in result.output


def test_site_command_fails_without_writing(config_path: Path, guides: Path, tmp_path: Path) -> None:
    (guides / "nolinks.md").write_text("---\nmembers_only: false\n---\n# No links\n", encoding="utf-8")
    out = tmp_path / "site"
    result = RUNNER.invoke(APP, ["site", "-o", str(out), "--config", str(config_path)], env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "1 problem found, so the site was not built" in result.output
    assert not out.exists()


def test_site_command_needs_a_folder(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["site", "-o", str(tmp_path / "site"), "--config", str(config_path)])
    assert result.exit_code == 1
    assert "guide.output_dir is not set" in result.output


def test_site_command_expands_home_before_protecting_the_guides(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / "site" / "guides").mkdir(parents=True)
    (home / "site" / SITE_MARKER).write_text("", encoding="utf-8")
    guide = write_guide(home / "site" / "guides", "keep.md", "aaaaaaaaaaa", members_only=False)
    monkeypatch.setenv("HOME", str(home))
    args = ["site", "~/site/guides", "-o", "~/site", "--config", str(config_path)]
    result = RUNNER.invoke(APP, args, env={"COLUMNS": "300"})
    assert result.exit_code == 1
    assert "holds the guide" in result.output
    assert guide.is_file()


def test_site_command_expands_home_in_the_output_folder(
    config_path: Path, guides: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(APP, ["site", str(guides), "--out=~/out", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert (home / "out" / "index.html").is_file()
    assert not (tmp_path / "~").exists()


def test_site_command_needs_an_output_folder(guides: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(), config_path)
    result = RUNNER.invoke(APP, ["site", str(guides), "--config", str(config_path)])
    assert result.exit_code == 1
    assert "site.output_dir is not set" in result.output
