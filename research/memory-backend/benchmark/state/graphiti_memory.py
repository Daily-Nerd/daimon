"""Graphiti (temporal knowledge graph) memory backend for the M0.3 spike.

This is the "adopt, don't build" arm: instead of our hand-rolled CSL merge, use
Graphiti — the temporal-KG engine behind Zep — which handles overrides via
bi-temporal edge invalidation (when a fact changes, the old edge gets an
`invalid_at` timestamp and the new one supersedes; search returns only valid
edges). That is the exact mechanism CSL's naive merge fumbled in M0.3.

Wiring (all on-prem / $0):
- graph backend: a FalkorDB instance (set FALKORDB_HOST/FALKORDB_PORT; e.g. a
  local container or a port-forwarded cluster service).
- entity/edge-extraction LLM: any OpenAI-compatible gateway (LITELLM_BASE_URL),
  same haiku-class model the CSL/summary arms used.
- embedder: local `fastembed` (BAAI/bge-small-en-v1.5, CPU, in-process) so we
  don't have to deploy an embeddings service (the proxy serves no embeddings).

Imports are lazy so this module is importable (and the rest of the suite runs)
without graphiti-core / fastembed installed. The first LIVE run may need small
signature tweaks against the installed graphiti-core version — its API moves.

Run deps:  uv run --with graphiti-core --with fastembed python ...
"""

import asyncio
import os
from typing import List, Optional

# Same conversational base time as nothing else depends on; turns advance it so
# the temporal graph has a real chronology (turn 1 precedes turn 7).
from datetime import datetime, timezone, timedelta

_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EMBED_DIM = 384

# Graphiti's default ontology extracts named entities (people, places, orgs)
# but silently drops attribute-style state — "favorite color is green",
# "budget is $40k" — which is exactly what the override probes test. Both
# Kimi and Haiku skipped such facts until told otherwise, so every episode
# carries this instruction (domain-agnostic; the standard adopter move per
# Zep's own custom-entity-type guidance).
_EXTRACTION_INSTRUCTIONS = (
    "Treat stated values as first-class entities: preferences, settings, "
    "quantities, budgets, deadlines, statuses, decisions, and choices (e.g. "
    "'green', '$40k', 'March 3rd'). Always record a fact edge linking the "
    "owner to the CURRENT value (e.g. 'Alice's favorite color is green'). "
    "When a value is corrected or changed, extract the new fact so it can "
    "supersede the old one."
)


def _make_embedder(model_name: str = DEFAULT_EMBED_MODEL):
    """Adapter from fastembed to Graphiti's EmbedderClient interface.

    Graphiti pydantic-validates the embedder as an EmbedderClient instance
    (duck typing is rejected), so the subclass is defined lazily here to keep
    graphiti_core out of module-level imports. Graphiti calls create()/
    create_batch() and expects list[float] / list[list[float]]; fastembed
    returns numpy arrays, which we coerce to lists.
    """
    from fastembed import TextEmbedding  # lazy
    from graphiti_core.embedder.client import EmbedderClient, EmbedderConfig

    class _FastEmbedEmbedder(EmbedderClient):
        def __init__(self):
            self._model = TextEmbedding(model_name)
            self.config = EmbedderConfig(embedding_dim=DEFAULT_EMBED_DIM)

        def _embed(self, texts: List[str]) -> List[List[float]]:
            return [list(map(float, v)) for v in self._model.embed(list(texts))]

        async def create(self, input_data):
            # Contract matches graphiti's OpenAIEmbedder: create() returns a
            # single vector even when input is a list (data[0].embedding);
            # only create_batch() returns one vector per input.
            texts = input_data if isinstance(input_data, list) else [input_data]
            vecs = await asyncio.to_thread(self._embed, texts)
            return vecs[0]

        async def create_batch(self, input_data_list):
            return await asyncio.to_thread(self._embed, list(input_data_list))

    return _FastEmbedEmbedder()


def _build_graphiti(group_id: str):
    """Construct a Graphiti client pointed at FalkorDB + LiteLLM + fastembed."""
    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.llm_client.config import LLMConfig

    base_url = os.environ["LITELLM_BASE_URL"]  # your OpenAI-compatible gateway; no default
    api_key = os.environ.get("LITELLM_VIRTUAL_KEY", "")
    model = os.environ.get("GRAPHITI_LLM_MODEL", "kimi-k2.6")
    fb_host = os.environ.get("FALKORDB_HOST", "localhost")
    fb_port = int(os.environ.get("FALKORDB_PORT", "6379"))

    config = LLMConfig(api_key=api_key, base_url=base_url + "/v1",
                       model=model, small_model=model)
    if model.startswith("claude-"):
        # Claude via LiteLLM's /v1/messages passthrough. Of the OpenAI-shaped
        # clients, OpenAIClient hits the Responses API (which the proxy doesn't
        # serve) and OpenAIGenericClient's json_schema response_format passes
        # through unenforced; AnthropicClient's tool-use structured output is
        # the most reliable path for Claude models.
        from anthropic import AsyncAnthropic
        from graphiti_core.llm_client.anthropic_client import AnthropicClient
        llm = AnthropicClient(
            config=config,
            client=AsyncAnthropic(api_key=api_key, base_url=base_url, max_retries=2),
        )
    else:
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
        # json_object, not the default json_schema: the proxy forwards the
        # json_schema response_format without enforcement, so models drift off
        # the schema's field names. In json_object mode graphiti injects the
        # schema into the prompt, which the model actually sees.
        llm = OpenAIGenericClient(config=config, structured_output_mode="json_object")
    driver = FalkorDriver(host=fb_host, port=fb_port, database=group_id)
    return Graphiti(graph_driver=driver, llm_client=llm, embedder=_make_embedder())


class GraphitiMemory:
    """Sync Memory facade (observe/context/tokens) over async Graphiti.

    One instance per scenario; `group_id` isolates each scenario's graph so
    facts from one don't leak into another.
    """
    name = "graphiti"

    def __init__(self, group_id: str):
        from benchmark.evaluate import count_tokens  # lazy, avoids hard dep at import
        self._count_tokens = count_tokens
        self.group_id = group_id
        self._turn_idx = 0
        self._last_context = ""
        self._loop = asyncio.new_event_loop()
        self._g = _build_graphiti(group_id)
        self._loop.run_until_complete(self._g.build_indices_and_constraints())

    def observe(self, turn: str) -> None:
        # A single flaky extraction (e.g. the model echoing the JSON schema
        # back, which pydantic rejects) must not kill a multi-hour run: retry,
        # then skip the turn and let grading penalize the lost facts.
        import logging
        import time as _time
        from graphiti_core.nodes import EpisodeType
        ref_time = _BASE_TIME + timedelta(minutes=self._turn_idx)
        self._turn_idx += 1
        for attempt in range(3):
            # The LiteLLM proxy caches responses by prompt, so a plain retry
            # of a failed extraction replays the same bad cached response.
            # Vary the instructions per attempt to force a cache miss and a
            # fresh sample (same trick as Daimon's parse-failure re-calls).
            instructions = _EXTRACTION_INSTRUCTIONS if attempt == 0 else (
                _EXTRACTION_INSTRUCTIONS
                + f" Re-extraction pass {attempt}: be precise and follow the output format exactly."
            )
            try:
                self._loop.run_until_complete(self._g.add_episode(
                    name=f"{self.group_id}-turn-{self._turn_idx}",
                    episode_body=turn,
                    source=EpisodeType.message,
                    source_description="benchmark conversation turn",
                    reference_time=ref_time,
                    group_id=self.group_id,
                    custom_extraction_instructions=instructions,
                ))
                return
            except Exception as e:  # noqa: BLE001 — any episode failure is non-fatal
                if attempt < 2:
                    _time.sleep(2 * (attempt + 1))
                    continue
                logging.getLogger(__name__).warning(
                    "[%s] add_episode failed after %d attempts, skipping turn %d: %s",
                    self.group_id, attempt + 1, self._turn_idx, e)

    def context(self, query: str) -> str:
        # Graphiti stamps superseded edges with invalid_at (bi-temporal
        # invalidation) but search() still RETURNS them — verified live: the
        # superseded fact comes back alongside its replacement. Current state
        # means filtering invalidated edges ourselves; the surviving facts are
        # the memory we hand to the answerer.
        edges = self._loop.run_until_complete(
            self._g.search(query=query, group_ids=[self.group_id])
        )
        facts = []
        for e in edges:
            if getattr(e, "invalid_at", None) is not None:
                continue
            fact = getattr(e, "fact", None) or str(e)
            facts.append(f"- {fact}")
        self._last_context = "\n".join(facts)
        return self._last_context

    def tokens(self) -> int:
        return self._count_tokens(self._last_context)

    def close(self) -> None:
        try:
            self._loop.run_until_complete(self._g.close())
        except Exception:
            pass
        finally:
            self._loop.close()
