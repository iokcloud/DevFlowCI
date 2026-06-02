"""LLM 工厂 — DeepSeek V4 thinking / JSON Output 配置。"""

from utils import create_llm, create_llm_json, create_llm_reasoning, create_llm_text


class TestCreateLlm:
    def test_json_profile(self):
        llm = create_llm_json(max_tokens=8192, temperature=0.2)
        assert llm.model_kwargs.get("response_format") == {"type": "json_object"}
        assert llm.extra_body == {"thinking": {"type": "disabled"}}
        assert llm.max_tokens == 8192
        assert llm.temperature == 0.2

    def test_reasoning_profile(self):
        llm = create_llm_reasoning()
        assert llm.extra_body.get("thinking") == {"type": "enabled"}
        assert "reasoning_effort" in llm.extra_body
        assert llm.model_kwargs.get("response_format") == {"type": "json_object"}

    def test_text_profile(self):
        llm = create_llm_text(temperature=0.1)
        assert llm.extra_body == {"thinking": {"type": "disabled"}}
        assert "response_format" not in llm.model_kwargs
        assert llm.temperature == 0.1

    def test_legacy_no_thinking_param(self):
        llm = create_llm(temperature=0.3)
        assert llm.temperature == 0.3
        assert not llm.extra_body
