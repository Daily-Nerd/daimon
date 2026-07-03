# Glossary (Semantic Nodes)

Terms of art used across the logbook. Keep definitions tight and Daimon-relevant.

## Daimon-specific

- **CRP — Cognitive Resumption Protocol.** The serialize-at-session-end → reconstruct-at-session-start cycle. Daimon's load-bearing bet.
- **Cognitive checkpoint.** The serialized state object written at session end (RFC §5.1 schema): open questions, decisions, beliefs, emotional valence, worker queue, memory pointers.
- **Dream sequence.** The skimmable narrative briefing generated from a checkpoint + retrieved memories at session start.
- **Confabulation (resurrection confabulation).** When reconstruction produces fluent, confident, but FALSE prior state. The core risk: lossy generative summarization invents plausible connective tissue with no internal error signal.
- **Epistemic graph.** Knowledge graph of user beliefs/decisions with relations (CONTRADICTS, SUPPORTS, SUPERSEDED_BY). Daimon's differentiator.
- **Initiative.** The agent's capacity to interrupt the user proactively, gated by an attention/confidence/relevance model.

## Memory & retrieval

- **RAG — Retrieval-Augmented Generation.** Fetch relevant external text, put it in the context window, generate conditioned on it. The whole paradigm Daimon sits in.
- **Embedding.** A fixed-length vector encoding the meaning of a text chunk; similar meaning → nearby vectors.
- **ANN — Approximate Nearest Neighbor.** Fast similarity search that trades exactness for speed.
- **HNSW — Hierarchical Navigable Small World.** A layered navigable graph for ANN; ~log(n) search. The default vector index.
- **IVF-PQ — Inverted File + Product Quantization.** Cluster + compress vectors; lower RAM, scales to billions, lower recall than HNSW.
- **BM25.** Classic lexical/keyword ranking. Catches exact terms semantic search misses.
- **Hybrid retrieval.** Combine lexical (BM25) + semantic (vector) results.
- **RRF — Reciprocal Rank Fusion.** Merge ranked lists by summing `1/(k+rank)`. Robust, tuning-free.
- **Cross-encoder (reranker).** Reads query+candidate together for high-accuracy scoring; too slow for the whole corpus, used on the top-k shortlist.
- **Bi-encoder.** Encodes query and document separately (the embedding model); fast, less accurate than a cross-encoder.
- **MMR — Maximal Marginal Relevance.** Diversifies results to avoid near-duplicate chunks.
- **Salience.** How "worth keeping/surfacing" a memory is. Generative Agents: recency · importance · relevance.
- **Reflection.** Generative Agents mechanism: periodically synthesize higher-level memories from raw observations.
- **Lost-in-the-middle.** LLMs attend strongly to context start/end, weakly to the middle → memory ordering matters.

## Knowledge & reasoning

- **NLI — Natural Language Inference.** Classify a premise/hypothesis pair as entailment / neutral / contradiction. Core contradiction-detection tool.
- **MNLI / SNLI / DocNLI.** Standard NLI training/eval datasets (sentence-pair; DocNLI = document-level).
- **DeBERTa.** Transformer commonly fine-tuned on MNLI as the workhorse NLI model.
- **OIE — Open Information Extraction.** Extract `(subject, predicate, object)` triplets from free text. Used for belief extraction.
- **Stance detection.** Classify a speaker's position (for/against/neutral) toward a target.
- **Temporal knowledge graph.** A KG where facts/relations have validity intervals — distinguishes belief EVOLUTION (sequential) from CONTRADICTION (simultaneous).
- **Louvain community detection.** Graph-clustering algorithm; used to find consistent belief clusters, with outliers flagged as contradictions.
- **Belief revision.** Formal logic of updating beliefs when new info conflicts with old.

## Infra (Hermes ecosystem)

- **Hermes.** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), MIT. The agent framework Daimon builds on. Provides memory, cron, multi-platform gateway, subagents, MCP, serverless execution.
- **Honcho.** Hermes' user-modeling layer. Possibly overlaps Daimon's epistemic graph — a key validation target.
- **MCP — Model Context Protocol.** Standard for connecting tools/data sources to LLM agents.
- **LiteLLM.** Model-routing layer; swap providers without code changes.
