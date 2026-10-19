"""The invoice-processing agent as a LangGraph state graph (blueprint section 10).

    extract ──(read failed)──────────────────────────────────────────────► finalize
       │
       ▼
    validate ──(errors, re-reads left, within budget)──► re_extract ──┐
       │  ▲                                                           │
       │  └───────────────────────────────────────────────────────────┘
       ▼ (clean, or out of re-reads/budget)
    save ► classify ► final_check ► decide ─(auto-approve)─► verify ► finalize
                                              └─(review)───────────────► finalize

verify: a second, differently rendered read must agree on every field (v1.2).

The loop is bounded twice: max_reextractions (2) and LangGraph's own recursion limit.
"""

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import AgentDeps, AgentState

# Far above the longest legal path (extract + 3 validates + 2 re-reads + 6 = 12 steps);
# if this is ever hit, it's a bug and LangGraph raises instead of looping forever.
RECURSION_LIMIT = 25


def build_graph(deps: AgentDeps):
    graph = StateGraph(AgentState)
    for name, node in [
        ("extract", nodes.extract),
        ("validate", nodes.check),
        ("re_extract", nodes.re_extract),
        ("save", nodes.save),
        ("classify", nodes.classify),
        ("final_check", nodes.final_check),
        ("decide", nodes.decide),
        ("verify", nodes.verify),
        ("finalize", nodes.finalize),
    ]:
        graph.add_node(name, partial(node, deps=deps))

    graph.add_edge(START, "extract")
    graph.add_conditional_edges("extract", nodes.after_extract, ["validate", "finalize"])
    graph.add_conditional_edges("validate", nodes.after_validate, ["re_extract", "save"])
    graph.add_edge("re_extract", "validate")
    graph.add_edge("save", "classify")
    graph.add_edge("classify", "final_check")
    graph.add_edge("final_check", "decide")
    graph.add_conditional_edges("decide", nodes.after_decide, ["verify", "finalize"])
    graph.add_edge("verify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_agent(deps: AgentDeps, invoice_id: int, pages) -> AgentState:
    """Process one invoice. The caller commits the session."""
    graph = build_graph(deps)
    initial: AgentState = {"invoice_id": invoice_id, "pages": pages, "path": []}
    return graph.invoke(initial, config={"recursion_limit": RECURSION_LIMIT})
