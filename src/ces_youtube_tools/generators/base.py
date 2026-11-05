"""The guide generator interface, and the spec that registers a generator with its options model."""

from pathlib import Path
from dataclasses import dataclass
from typing import Protocol
from collections.abc import Mapping, Callable

from pydantic import BaseModel

from ..video import VideoInfo

SYSTEM_PROMPT = (
    "You write watching guides for videos from their transcripts, following the user's format instructions exactly. "
    "Reply with the Markdown guide only."
)


def refuse_recorded_flags(extra_args: list[str], hints: Mapping[str, str]) -> list[str]:
    """Refuse flags in a generator's `extra_args` that would change what the front matter records.

    `extra_args` come last on the command line, so a `--model` there would override the `model` option without the
    front matter knowing.

    Args:
        extra_args (list[str]): The configured arguments.
        hints (Mapping[str, str]): The refused flags, such as `--model`, each with what to do instead, such as "set
            the model option instead". A flag is refused alone or as `--model=...`.

    Returns:
        list[str]: The arguments, unchanged.

    Raises:
        ValueError: If an argument is one of the refused flags, with or without `=...`.
    """
    for arg in extra_args:
        flag = arg.split("=", 1)[0]
        if flag in hints:
            raise ValueError(f"extra_args cannot hold {flag}: {hints[flag]}")
    return extra_args


class GuideGenerator(Protocol):
    """Writes a Markdown guide from a prompt, e.g. with the `claude` CLI or an LLM API.

    `harness`, `model` and `effort` are known before the run, from the generator and its options, and are recorded in
    the guide's front matter.

    Attributes:
        harness (str): The tool the generator runs, such as `Claude Code`.
        model (str): The model passed to it, as configured (an alias such as `opus` stays an alias).
        effort (str | None): The effort level passed to it, or None if the generator has no effort setting.
    """

    @property
    def harness(self) -> str:
        """str: The tool the generator runs, such as `Claude Code`."""
        ...

    @property
    def model(self) -> str:
        """str: The model passed to the tool, as configured."""
        ...

    @property
    def effort(self) -> str | None:
        """str | None: The effort level passed to the tool, or None if the generator has no effort setting."""
        ...

    def generate(self, prompt: str, video: VideoInfo, work_dir: Path) -> str:
        """Generate the guide.

        Args:
            prompt (str): The full prompt, including the transcript.
            video (VideoInfo): The video the guide is for.
            work_dir (Path): The video's work directory.

        Returns:
            str: The generated Markdown.

        Raises:
            GuideError: If generation fails.
        """
        ...


@dataclass(frozen=True)
class GeneratorSpec[OptionsT: BaseModel]:
    """Registers a guide generator: its configuration name, its options model, and how to build it.

    Attributes:
        name (str): The generator's name in `guide.generator`, and its `[guide.options.<name>]` table.
        options_model (type[OptionsT]): The model of its `[guide.options.<name>]` table. `config options` lists its
            fields.
        build (Callable[[OptionsT], GuideGenerator]): Makes the generator from validated options.
    """

    name: str
    options_model: type[OptionsT]
    build: Callable[[OptionsT], GuideGenerator]

    def create(self, raw: Mapping[str, object]) -> GuideGenerator:
        """Validate the generator's settings and make the generator.

        Keeping the options type inside the spec lets one registry hold specs with different options models.

        Args:
            raw (Mapping[str, object]): The `[guide.options.<name>]` table, as read from the configuration file.

        Returns:
            GuideGenerator: The generator.

        Raises:
            ValidationError: If the settings do not match `options_model`.
        """
        return self.build(self.options_model.model_validate(raw))
