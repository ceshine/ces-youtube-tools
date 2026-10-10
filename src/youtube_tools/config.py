"""Configuration models and TOML persistence."""

import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import Field, BaseModel, ValidationError, field_validator

CONFIG_DIR = Path.home() / ".config" / "ces-youtube-tools"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_PROMPT_TEMPLATE_PATH = CONFIG_DIR / "guide_prompt.md"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "ces-youtube-tools"
DEFAULT_YTDLP_COMMAND = ["uvx", "--from", "yt-dlp[default,curl-cffi]@latest", "yt-dlp"]


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


def _expand_path(value: Path | None) -> Path | None:
    """Expand a leading `~` in an optional path.

    Args:
        value (Path | None): The path to expand.

    Returns:
        Path | None: The expanded path, or None when no path was given.
    """
    return value.expanduser() if value is not None else None


class AuthConfig(BaseModel):
    """Where YouTube cookies come from.

    Attributes:
        firefox_profile (Path | None): Folder of the Firefox profile signed in to YouTube.
    """

    firefox_profile: Path | None = None

    expand_paths = field_validator("firefox_profile")(_expand_path)


class ChannelConfig(BaseModel):
    """A frequently used channel.

    Attributes:
        name (str): Display name of the channel.
        url (str): Canonical channel URL, e.g. `https://www.youtube.com/@handle`.
        channel_id (str): YouTube channel ID (starts with `UC`).
        members (bool): Whether listings include members-only videos. Turn on only for channels you are a member of,
            since members-only videos cannot be downloaded otherwise.
    """

    name: str
    url: str
    channel_id: str
    members: bool = False


class TranscriptConfig(BaseModel):
    """Transcript provider settings.

    Attributes:
        provider (str): Name of the transcript provider.
        languages (list[str]): Subtitle languages to try, in order of preference.
    """

    provider: str = "yt-dlp-subs"
    languages: list[str] = Field(default_factory=lambda: ["en-orig", "en"])


class GuideConfig(BaseModel):
    """Watching guide generator settings.

    Attributes:
        generator (str): Name of the guide generator.
        prompt_template (Path): User-editable prompt template file.
        output_dir (Path | None): Where guides are written. None means the current working directory.
        options (dict[str, dict[str, Any]]): Per-generator settings, keyed by generator name.
    """

    generator: str = "claude-cli"
    prompt_template: Path = DEFAULT_PROMPT_TEMPLATE_PATH
    output_dir: Path | None = None
    options: dict[str, dict[str, Any]] = Field(default_factory=dict)

    expand_paths = field_validator("prompt_template", "output_dir")(_expand_path)


class AppConfig(BaseModel):
    """Top-level configuration.

    Attributes:
        ytdlp_command (list[str]): Command used to run yt-dlp.
        cache_dir (Path): Root of the per-video work directories (metadata, subtitles, transcripts).
        auth (AuthConfig): Cookie source settings.
        channels (list[ChannelConfig]): Frequently used channels.
        transcript (TranscriptConfig): Transcript provider settings.
        guide (GuideConfig): Guide generator settings.
    """

    ytdlp_command: list[str] = Field(default_factory=lambda: list(DEFAULT_YTDLP_COMMAND))
    cache_dir: Path = DEFAULT_CACHE_DIR
    auth: AuthConfig = Field(default_factory=AuthConfig)
    channels: list[ChannelConfig] = Field(default_factory=list)
    transcript: TranscriptConfig = Field(default_factory=TranscriptConfig)
    guide: GuideConfig = Field(default_factory=GuideConfig)

    expand_paths = field_validator("cache_dir")(_expand_path)

    def find_channel(self, key: str) -> ChannelConfig | None:
        """Find a channel by name (case-insensitive) or channel ID.

        Args:
            key (str): Channel name or channel ID.

        Returns:
            ChannelConfig | None: The matching channel, or None if there is no match.
        """
        for channel in self.channels:
            if channel.channel_id == key or channel.name.casefold() == key.casefold():
                return channel
        return None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and validate the configuration file.

    Args:
        path (Path): Path of the TOML configuration file.

    Returns:
        AppConfig: The validated configuration.

    Raises:
        ConfigError: If the file does not exist, is not valid TOML or fails validation.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}. Run `ces-youtube-tools config init` first.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(data)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid TOML: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"Config file {path} is invalid:\n{exc}") from exc


def dump_config(config: AppConfig) -> str:
    """Serialize the configuration to TOML.

    Args:
        config (AppConfig): The configuration to serialize.

    Returns:
        str: The TOML document. Unset optional values are omitted.
    """
    return tomli_w.dumps(config.model_dump(mode="json", exclude_none=True))


def save_config(config: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Write the configuration file, creating its directory if needed.

    Args:
        config (AppConfig): The configuration to save.
        path (Path): Path of the TOML configuration file.

    Returns:
        None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(config), encoding="utf-8")
