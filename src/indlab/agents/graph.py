"""The agent graph: one supervisor, five specialists, one async entry point.

What changed, and why it matters
--------------------------------
The previous implementation ran the supervisor, threw its answer away, parsed
the message list looking for ``{"next": ...}`` JSON that langgraph-supervisor
never emits, and then invoked a second agent by hand. Every request therefore
cost two LLM round trips and usually ended in "🤔 Не понял запроса". Worse,
both calls were the *synchronous* ``.invoke()`` made from inside an async
handler, so one user's request froze the bot for everyone.

Now there is a single graph, delegation happens through the framework's own
handoff tools, and the entry point is ``await``-ed. Conversation state lives in
a checkpointer keyed by the artist, so the copilot remembers the thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import create_react_agent
from langgraph_supervisor import create_supervisor

from indlab.agents.llm import build_llm
from indlab.agents.prompts import (
    CONSULTANT_PROMPT,
    DONE_SENTINEL,
    MARKETING_PROMPT,
    OPEN_CALL_PROMPT,
    PLANNER_PROMPT,
    PORTFOLIO_PROMPT,
    SUPERVISOR_PROMPT,
)
from indlab.tools.deliverables import (
    PLANNER_TOOLS,
    PORTFOLIO_TOOLS,
    PROGRESS_TOOLS,
    STRATEGY_TOOLS,
)
from indlab.tools.knowledge import KNOWLEDGE_TOOLS
from indlab.tools.opencalls import OPEN_CALL_TOOLS
from indlab.tools.profile import PROFILE_TOOLS

log = logging.getLogger(__name__)

CONSULTANT = "ConsultantAgent"
PORTFOLIO = "PortfolioAnalyzerAgent"
MARKETING = "MarketingAgent"
PLANNER = "PlannerAgent"
OPEN_CALLS = "OpenCallAgent"
SUPERVISOR = "Supervisor"

AGENT_NAMES = frozenset({CONSULTANT, PORTFOLIO, MARKETING, PLANNER, OPEN_CALLS})

FALLBACK_REPLY = (
    "Что-то пошло не так на моей стороне. Попробуй переспросить чуть иначе — "
    "или напиши /help, и я подскажу, что умею."
)


def _message_text(message: BaseMessage) -> str:
    """Content as plain text, tolerating both string and block content."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")


def extract_answer(messages: list[BaseMessage], agent_names: frozenset[str]) -> str:
    """Pick the message the artist should actually see.

    A specialist's reply wins over the supervisor's closing turn, so the
    artist gets the expert wording rather than a paraphrase of it. Only
    messages produced after the most recent user turn are considered.
    """
    recent: list[BaseMessage] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        recent.append(message)

    def usable(message: BaseMessage) -> str | None:
        if not isinstance(message, AIMessage):
            return None
        text = _message_text(message).strip()
        if not text or text.strip(" .!…").upper() == DONE_SENTINEL:
            return None
        return text

    for message in recent:
        if getattr(message, "name", None) in agent_names and (text := usable(message)):
            return text
    for message in recent:
        if text := usable(message):
            return text
    return FALLBACK_REPLY


def build_agents(llm: BaseChatModel) -> list:
    """Create the five specialists.

    Every agent carries the profile tools: whichever specialist happens to
    learn a fact about the artist records it, so the artist never repeats
    themselves.
    """
    return [
        create_react_agent(
            llm,
            [*KNOWLEDGE_TOOLS, *PROFILE_TOOLS, *PROGRESS_TOOLS],
            prompt=CONSULTANT_PROMPT,
            name=CONSULTANT,
        ),
        create_react_agent(
            llm,
            [*PORTFOLIO_TOOLS, *PROFILE_TOOLS],
            prompt=PORTFOLIO_PROMPT,
            name=PORTFOLIO,
        ),
        create_react_agent(
            llm,
            [*STRATEGY_TOOLS, *PROFILE_TOOLS],
            prompt=MARKETING_PROMPT,
            name=MARKETING,
        ),
        create_react_agent(
            llm,
            [*PLANNER_TOOLS, *PROFILE_TOOLS],
            prompt=PLANNER_PROMPT,
            name=PLANNER,
        ),
        create_react_agent(
            llm,
            [*OPEN_CALL_TOOLS, *PROFILE_TOOLS],
            prompt=OPEN_CALL_PROMPT,
            name=OPEN_CALLS,
        ),
    ]


@dataclass(slots=True)
class Copilot:
    """Thin async facade over the compiled graph."""

    graph: object
    agent_names: frozenset[str] = AGENT_NAMES

    async def ask(self, text: str, *, thread_id: str, recursion_limit: int = 25) -> str:
        """Answer one message from the artist.

        The caller must already be inside an ``artist_scope``; tools read the
        identity from there rather than from the model.
        """
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit,
        }
        try:
            result = await self.graph.ainvoke({"messages": [HumanMessage(content=text)]}, config)
        except Exception:
            log.exception("Agent run failed for thread %s", thread_id)
            return FALLBACK_REPLY
        return extract_answer(list(result.get("messages", [])), self.agent_names)


def build_copilot(
    llm: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Copilot:
    """Compile the supervisor graph."""
    llm = llm or build_llm()
    workflow = create_supervisor(
        build_agents(llm),
        model=llm,
        prompt=SUPERVISOR_PROMPT,
        supervisor_name=SUPERVISOR,
        output_mode="last_message",
        # Keeps the transcript clean: without this every handoff injects a
        # "transferring back" message that looks like an agent answer.
        add_handoff_back_messages=False,
    )
    graph = workflow.compile(checkpointer=checkpointer, name="IndlabCopilot")
    log.info("Copilot graph compiled with agents: %s", ", ".join(sorted(AGENT_NAMES)))
    return Copilot(graph=graph)
