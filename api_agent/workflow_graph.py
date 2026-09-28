"""LangGraph topology, independent of domain tools and their file formats."""

from collections.abc import Callable, Mapping
from operator import itemgetter
from typing import Any

from langgraph.graph import END, START, StateGraph

from api_agent.graph_state import V2State


NODE_NAMES = (
    "parse_requirement", "review_requirement", "analyze_requirements",
    "design_cases", "review_coverage", "generate_script", "review_script",
    "contract_gate", "regenerate_affected", "execute", "review_results", "finalize",
)


def build_workflow_graph(nodes: Mapping[str, Callable[..., Any]]) -> StateGraph:
    graph = StateGraph(V2State)
    for name in NODE_NAMES:
        graph.add_node(name, nodes[name])

    for source, target in (
        (START, "parse_requirement"),
        ("parse_requirement", "review_requirement"),
        ("analyze_requirements", "design_cases"),
        ("design_cases", "review_coverage"),
        ("generate_script", "review_script"),
        ("regenerate_affected", "contract_gate"),
        ("execute", "review_results"),
        ("finalize", END),
    ):
        graph.add_edge(source, target)

    routes = {
        "review_requirement": {"approved": "analyze_requirements", "retry": "parse_requirement", "human": "finalize"},
        "review_coverage": {"approved": "generate_script", "retry": "design_cases", "human": "finalize"},
        "review_script": {"approved": "contract_gate", "retry": "generate_script", "human": "finalize"},
        "contract_gate": {"continue": "execute", "regenerate": "regenerate_affected", "human": "finalize"},
        "review_results": {"approved": "finalize", "retry": "execute", "human": "finalize"},
    }
    for source, destinations in routes.items():
        graph.add_conditional_edges(source, itemgetter("route"), destinations)
    return graph
