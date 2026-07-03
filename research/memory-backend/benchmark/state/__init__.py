"""State-tracking benchmark (ROADMAP M0.3).

Tests whether a memory representation maintains *current* agent state across a
multi-turn conversation with overrides — not factual recall. The discriminating
comparison is CSL (structured) vs a running prose summary, both consolidated by
the same model at the same token budget, so the only variable is representation.
"""
