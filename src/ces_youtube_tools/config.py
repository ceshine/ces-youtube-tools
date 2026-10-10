"""Configuration models and TOML persistence."""

import re
import json
import tomllib
from pathlib import Path
from dataclasses import dataclass
from typing import Any, get_args

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

    firefox_profile: Path | None = Field(
        default=None, description="Folder of the Firefox profile signed in to YouTube, used for cookies."
    )

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

    name: str = Field(description="Display name of the channel.")
    url: str = Field(description="Canonical channel URL, e.g. https://www.youtube.com/@handle.")
    channel_id: str = Field(description="YouTube channel ID (starts with UC).")
    members: bool = Field(default=False, description="Also list members-only videos. Only for channels you belong to.")


class TranscriptConfig(BaseModel):
    """Transcript provider settings.

    Attributes:
        provider (str): Name of the transcript provider.
        languages (list[str]): Subtitle languages to try, in order of preference.
    """

    provider: str = Field(default="yt-dlp-subs", description="Name of the transcript provider.")
    languages: list[str] = Field(
        default_factory=lambda: ["en-orig", "en"], description="Subtitle languages to try, in order of preference."
    )


class GuideConfig(BaseModel):
    """Watching guide generator settings.

    Attributes:
        generator (str): Name of the guide generator.
        prompt_template (Path): User-editable prompt template file.
        output_dir (Path | None): Where guides are written. None means the current working directory.
        options (dict[str, dict[str, Any]]): Per-generator settings, keyed by generator name.
    """

    generator: str = Field(default="claude-cli", description="Name of the guide generator.")
    prompt_template: Path = Field(
        default=DEFAULT_PROMPT_TEMPLATE_PATH, description="User-editable prompt template file."
    )
    output_dir: Path | None = Field(
        default=None, description="Where guides are written. Unset means the current working directory."
    )
    options: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-generator settings, in `[guide.options.<generator>]` tables."
    )

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

    ytdlp_command: list[str] = Field(
        default_factory=lambda: list(DEFAULT_YTDLP_COMMAND), description="Command used to run yt-dlp."
    )
    cache_dir: Path = Field(default=DEFAULT_CACHE_DIR, description="Root of the per-video work directories.")
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Cookie source settings.")
    channels: list[ChannelConfig] = Field(default_factory=list, description="Frequently used channels.")
    transcript: TranscriptConfig = Field(default_factory=TranscriptConfig, description="Transcript provider settings.")
    guide: GuideConfig = Field(default_factory=GuideConfig, description="Guide generator settings.")

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


@dataclass(frozen=True)
class OptionInfo:
    """One configuration option, as shown by `config options`.

    Attributes:
        key (str): Dotted TOML key, e.g. `guide.output_dir`. `[]` marks an array of tables.
        type_name (str): Readable type, e.g. `list[str]` or `Path | None`.
        default (str): Default value, `(required)` or `(unset)`.
        description (str): What the option does.
    """

    key: str
    type_name: str
    default: str
    description: str


def _type_name(annotation: Any) -> str:
    """Format a field annotation for display.

    Args:
        annotation (Any): The field's type annotation.

    Returns:
        str: The type without module prefixes, e.g. `Path | None`.
    """
    if isinstance(annotation, type) and not get_args(annotation):
        return annotation.__name__
    return re.sub(r"\b(pathlib|typing)\.(_local\.)?", "", str(annotation))


def _format_default(value: Any) -> str:
    """Format a default value the way it would be written in the TOML file.

    Args:
        value (Any): The default value.

    Returns:
        str: `(unset)` for None, else the value as TOML-compatible text.
    """
    if value is None:
        return "(unset)"
    return json.dumps(value, default=str)


def describe_options(model: type[BaseModel], prefix: str = "") -> list[OptionInfo]:
    """List the options of a configuration model, descending into nested tables.

    Args:
        model (type[BaseModel]): The model class to describe.
        prefix (str): Dotted key of the model's table, empty for the top level.

    Returns:
        list[OptionInfo]: One entry per option, in declaration order. A table is listed before its own options.
    """
    options: list[OptionInfo] = []
    for name, field in model.model_fields.items():
        key = f"{prefix}{name}"
        annotation = field.annotation
        nested = annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None
        item_args = get_args(annotation)
        if (
            nested is None
            and len(item_args) == 1
            and isinstance(item_args[0], type)
            and issubclass(item_args[0], BaseModel)
        ):
            key, nested = f"{key}[]", item_args[0]
        default = "(required)" if field.is_required() else _format_default(field.get_default(call_default_factory=True))
        if nested is not None:
            options.append(
                OptionInfo(key, "table" if not key.endswith("[]") else "array of tables", "", field.description or "")
            )
            options.extend(describe_options(nested, f"{key}."))
        else:
            options.append(OptionInfo(key, _type_name(annotation), default, field.description or ""))
    return options
