"""Guide generators, chosen by the name in `guide.generator`.

A new generator adds its spec to `GUIDE_GENERATORS`, and its spec type to `AnyGeneratorSpec`. Generator modules never
import this package's registry, so the imports only go one way.
"""

from pydantic import ValidationError

from .base import GeneratorSpec, GuideGenerator
from .pi_cli import PiCliOptions, GENERATOR_NAME as PI_CLI_GENERATOR, PiCliGenerator
from .agy_cli import AgyCliOptions, GENERATOR_NAME as AGY_CLI_GENERATOR, AgyCliGenerator
from .claude_cli import GENERATOR_NAME as CLAUDE_CLI_GENERATOR, ClaudeCliOptions, ClaudeCliGenerator
from ..config import AppConfig

# A union of the registered spec types, one per generator (`GeneratorSpec[A] | GeneratorSpec[B]`). A spec is
# invariant in its options type, so `GeneratorSpec[BaseModel]` would not accept them.
AnyGeneratorSpec = GeneratorSpec[ClaudeCliOptions] | GeneratorSpec[AgyCliOptions] | GeneratorSpec[PiCliOptions]

GUIDE_GENERATORS: dict[str, AnyGeneratorSpec] = {
    spec.name: spec
    for spec in (
        GeneratorSpec(CLAUDE_CLI_GENERATOR, ClaudeCliOptions, ClaudeCliGenerator),
        GeneratorSpec(AGY_CLI_GENERATOR, AgyCliOptions, AgyCliGenerator),
        GeneratorSpec(PI_CLI_GENERATOR, PiCliOptions, PiCliGenerator),
        # A new generator adds its spec here.
    )
}


def make_guide_generator(config: AppConfig) -> GuideGenerator:
    """Build the guide generator named in the configuration.

    Args:
        config (AppConfig): The configuration.

    Returns:
        GuideGenerator: The generator.

    Raises:
        ValueError: If the generator name is unknown or its `[guide.options.<name>]` settings are invalid.
    """
    name = config.guide.generator
    spec = GUIDE_GENERATORS.get(name)
    if spec is None:
        raise ValueError(f"Unknown guide generator {name!r}. Available: {', '.join(sorted(GUIDE_GENERATORS))}.")
    try:
        return spec.create(config.guide.options.get(name, {}))
    except ValidationError as exc:
        raise ValueError(f"Invalid settings in [guide.options.{name}]:\n{exc}") from exc
