"""The guide generator interface, and the spec that registers a generator with its options model."""

from pathlib import Path
from dataclasses import dataclass
from typing import Protocol
from collections.abc import Mapping, Callable

from pydantic import BaseModel

from ..video import VideoInfo


class GuideGenerator(Protocol):
    """Writes a Markdown guide from a prompt, e.g. with the `claude` CLI or an LLM API."""

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
