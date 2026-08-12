"""Agent-layer wiring, without touching the network.

``extract_answer`` is the piece that replaced the old hand-rolled router, so
it gets the coverage the router never had.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from indlab.agents.graph import AGENT_NAMES, FALLBACK_REPLY, extract_answer
from indlab.agents.prompts import DONE_SENTINEL, SUPERVISOR_PROMPT


def test_specialist_answer_wins_over_supervisor_closing_turn():
    messages = [
        HumanMessage(content="разбери портфолио"),
        AIMessage(content="Вот разбор твоих работ.", name="PortfolioAnalyzerAgent"),
        AIMessage(content=DONE_SENTINEL, name="Supervisor"),
    ]
    assert extract_answer(messages, AGENT_NAMES) == "Вот разбор твоих работ."


def test_supervisor_answers_directly_when_nobody_was_delegated_to():
    messages = [
        HumanMessage(content="привет"),
        AIMessage(content="Привет! Я помогаю художникам.", name="Supervisor"),
    ]
    assert extract_answer(messages, AGENT_NAMES) == "Привет! Я помогаю художникам."


def test_handoff_tool_traffic_is_ignored():
    messages = [
        HumanMessage(content="какие опенколы"),
        AIMessage(content="", name="Supervisor"),
        ToolMessage(content="Successfully transferred", tool_call_id="1", name="transfer"),
        AIMessage(content="Нашла три подходящих.", name="OpenCallAgent"),
        AIMessage(content=DONE_SENTINEL, name="Supervisor"),
    ]
    assert extract_answer(messages, AGENT_NAMES) == "Нашла три подходящих."


def test_sentinel_with_punctuation_is_still_discarded():
    messages = [
        HumanMessage(content="?"),
        AIMessage(content="Ответ специалиста.", name="MarketingAgent"),
        AIMessage(content=f"{DONE_SENTINEL}.", name="Supervisor"),
    ]
    assert extract_answer(messages, AGENT_NAMES) == "Ответ специалиста."


def test_only_messages_after_the_last_user_turn_are_considered():
    messages = [
        HumanMessage(content="первый вопрос"),
        AIMessage(content="старый ответ", name="ConsultantAgent"),
        HumanMessage(content="второй вопрос"),
        AIMessage(content="новый ответ", name="ConsultantAgent"),
    ]
    assert extract_answer(messages, AGENT_NAMES) == "новый ответ"


def test_empty_run_falls_back_instead_of_crashing():
    assert extract_answer([], AGENT_NAMES) == FALLBACK_REPLY


def test_only_sentinel_falls_back():
    messages = [HumanMessage(content="?"), AIMessage(content=DONE_SENTINEL, name="Supervisor")]
    assert extract_answer(messages, AGENT_NAMES) == FALLBACK_REPLY


def test_block_style_content_is_flattened():
    messages = [
        HumanMessage(content="?"),
        AIMessage(
            content=[{"type": "text", "text": "Ответ "}, {"type": "text", "text": "блоками."}],
            name="ConsultantAgent",
        ),
    ]
    assert extract_answer(messages, AGENT_NAMES) == "Ответ блоками."


def test_supervisor_prompt_does_not_ask_for_routing_json():
    """The old prompt demanded {"next": ...}, which the framework never emits."""
    assert '{"next"' not in SUPERVISOR_PROMPT
    assert "JSON" not in SUPERVISOR_PROMPT


def test_every_agent_named_in_the_prompt_exists():
    for name in AGENT_NAMES:
        assert name in SUPERVISOR_PROMPT, f"{name} is never mentioned to the router"


def _offline_model():
    """A real ChatOpenAI instance with a dummy key.

    Constructing it issues no request; we only ever build the graph, never run
    it, so the suite stays offline while still exercising the real code path
    (a fake chat model cannot bind tools).
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-4o-mini", api_key="test-key-never-used")


def test_agents_are_constructed_with_their_tools():
    from indlab.agents.graph import build_agents

    agents = build_agents(_offline_model())
    assert {agent.name for agent in agents} == AGENT_NAMES


def test_supervisor_graph_compiles():
    """The whole graph must assemble — the old build never got this far."""
    from indlab.agents.graph import build_copilot

    copilot = build_copilot(llm=_offline_model())
    assert copilot.graph is not None
    assert copilot.agent_names == AGENT_NAMES
