import pytest

from ces_youtube_tools.config import AppConfig
from ces_youtube_tools.generators import make_guide_generator
from ces_youtube_tools.generators.agy_cli import AgyCliGenerator
from ces_youtube_tools.generators.claude_cli import ClaudeCliGenerator


def test_make_guide_generator() -> None:
    config = AppConfig()
    config.guide.options["claude-cli"] = {"model": "opus"}
    generator = make_guide_generator(config)
    assert isinstance(generator, ClaudeCliGenerator)
    assert generator.options.model == "opus"


def test_make_guide_generator_agy_cli() -> None:
    config = AppConfig()
    config.guide.generator = "agy-cli"
    config.guide.options["agy-cli"] = {"model": "claude-sonnet-4-6"}
    generator = make_guide_generator(config)
    assert isinstance(generator, AgyCliGenerator)
    assert generator.options.model == "claude-sonnet-4-6"


def test_make_guide_generator_errors() -> None:
    config = AppConfig()
    config.guide.generator = "openai"
    with pytest.raises(ValueError, match="Unknown guide generator 'openai'. Available: agy-cli, claude-cli"):
        _ = make_guide_generator(config)
    config = AppConfig()
    config.guide.options["claude-cli"] = {"modle": "opus"}
    with pytest.raises(ValueError, match=r"Invalid settings in \[guide.options.claude-cli\]"):
        _ = make_guide_generator(config)
