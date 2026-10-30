from bicopilot.llm import LLM


class ExplodingLLM(LLM):
    name = "openai"  # pretend to be the model path

    def structured(self, *a, **k):
        raise RuntimeError("simulated provider outage")

    def react(self, *a, **k):
        raise RuntimeError("simulated provider outage")


def test_model_failure_degrades_to_deterministic_answer(copilot, monkeypatch):
    from bicopilot.agents.graph import build_graph
    from bicopilot.agents.state import Runtime
    from bicopilot.llm import FakeLLM

    exploding = ExplodingLLM()
    rt = Runtime(copilot.settings, copilot.semantic, copilot.retriever, exploding, copilot.engine)
    monkeypatch.setattr(copilot, "llm", exploding)
    monkeypatch.setattr(copilot, "graph", build_graph(rt))
    fallback_rt = Runtime(
        copilot.settings, copilot.semantic, copilot.retriever, FakeLLM(copilot.semantic), copilot.engine
    )
    monkeypatch.setattr(copilot, "fallback_graph", build_graph(fallback_rt))
    copilot.cache.clear()
    r = copilot.ask("revenue by region 2017")
    assert r["status"] == "answered" and r["row_count"] == 5
    assert r["llm"] == "fake (fallback)" and "simulated provider outage" in r["fallback_error"]
    assert "deterministic compiler answered" in r["answer"]
    assert copilot.ask("revenue by region 2017")["cached"] is False  # degraded answers are not cached
