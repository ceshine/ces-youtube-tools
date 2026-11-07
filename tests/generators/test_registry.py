import pytest

from ces_youtube_tools.config import AppConfig
from ces_youtube_tools.generators import make_guide_generator
from ces_youtube_tools.generators.pi_cli import PiCliGenerator
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
    config.guide.options["agy-cli"] = {"model": "gemini-3.8-flash-high"}
    generator = make_guide_generator(config)
    assert isinstance(generator, AgyCliGenerator)
    assert generator.options.model == "gemini-3.8-flash-high"


def test_make_guide_generator_pi_cli() -> None:
    config = AppConfig()
    config.guide.generator = "pi-cli"
    config.guide.options["pi-cli"] = {"model": "openrouter/openai/gpt-6.1-terra", "effort": "medium"}
    generator = make_guide_generator(config)
    assert isinstance(generator, PiCliGenerator)
    assert (generator.options.model, generator.options.effort) == ("openrouter/openai/gpt-6.1-terra", "medium")


def test_make_guide_generator_errors() -> None:
    config = AppConfig()
    config.guide.generator = "openai"
    with pytest.raises(ValueError, match="Unknown guide generator 'openai'. Available: agy-cli, claude-cli, pi-cli"):
        _ = make_guide_generator(config)
    config = AppConfig()
    config.guide.options["claude-cli"] = {"modle": "opus"}
    with pytest.raises(ValueError, match=r"Invalid settings in \[guide.options.claude-cli\]"):
        _ = make_guide_generator(config)
