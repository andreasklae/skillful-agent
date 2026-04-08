from skill_agent.models import AgentConfig


def test_context_compression_threshold_default():
    cfg = AgentConfig()
    assert cfg.context_compression_threshold == 100_000


def test_context_compression_threshold_custom():
    cfg = AgentConfig(context_compression_threshold=50_000)
    assert cfg.context_compression_threshold == 50_000
