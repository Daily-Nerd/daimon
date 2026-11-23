// The landing's briefing text. It is rendered by scripts/sync_briefing_samples.py
// from the real briefing renderer; do not type sample text into a component.
import sample from './generated/briefing-sample.json';

export type SampleKind = 'verbatim' | 'inferred' | 'stale';

export function sampleItem(kind: SampleKind) {
  const found = sample.landing.items.find((i) => i.kind === kind);
  if (!found) {
    throw new Error(`briefing sample has no ${kind} item`);
  }
  return found;
}
