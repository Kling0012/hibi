"""Synthetic A/B of Graphiti's node prompt, without any graph writes.

Run inside the Gateway container: python -m evaluation.verify_graphiti_jev
or pass this module to python on stdin. Outputs counts/timings, not API keys.
This evaluates the adapter stage, NOT full add_episode performance.
"""
import asyncio
import json
import time

from graphiti_core.prompts import dedupe_nodes
from graphiti_core.prompts.dedupe_nodes import NodeResolutions
from gateway.app.config import Settings
from gateway.app.graph_store import build_llm_client


async def main():
    context = {
        "episode_content": "Project Cedar runs its production database on AmberDB. The abbreviation ADB below refers specifically to that same AmberDB database. The staging database BlueDB is separate.",
        "previous_episodes": [],
        "extracted_nodes": [{"id": 0, "name": "ADB", "entity_type": ["Technology"]}, {"id": 1, "name": "BlueDB", "entity_type": ["Technology"]}],
        "existing_nodes": [{"candidate_id": 0, "name": "AmberDB", "entity_types": ["Technology"], "summary": "Project Cedar's production database, abbreviated ADB."}],
    }
    outputs = {}
    for provider in ("llm", "jev"):
        settings = Settings(memory_ingestion_mode="graphiti", memory_node_resolution_provider=provider)
        client = build_llm_client(settings)
        started = time.monotonic()
        try:
            response = await client.generate_response(dedupe_nodes.nodes(context), response_model=NodeResolutions, prompt_name="dedupe_nodes.nodes")
            resolutions = NodeResolutions.model_validate(response).entity_resolutions
            actual = {r.id: r.duplicate_candidate_id for r in resolutions}
            assert actual == {0: 0, 1: -1}, (provider, actual)
            outputs[provider] = {"seconds": time.monotonic()-started, "correct": True, "decisions": actual,
                                 "routing": dict(client.node_router.metrics) if client.node_router else {}}
        finally:
            await client.client.close()
    print(json.dumps({"stage": "node_resolution_only", "results": outputs}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
