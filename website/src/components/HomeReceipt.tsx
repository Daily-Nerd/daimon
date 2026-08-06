import type {ReactNode} from 'react';

export default function HomeReceipt(): ReactNode {
  return (
    <div className="receipt" role="img" aria-label="Example daimon briefing with verified, inferred, and stale items">
      <div className="receiptHead">
        DAIMON BRIEFING <span>written 12m ago</span>
      </div>
      <div className="receiptRow">
        <span className="tv">✔ verbatim</span> "retry uses exponential backoff, cap 30s"
      </div>
      <div className="receiptSub">
        └ quote verified · transcript line 214 <span className="chip chipOk">✔ checked</span>
      </div>
      <div className="receiptRow">
        <span className="ti">~ inferred</span> cache layer is the bottleneck
      </div>
      <div className="receiptRow">
        <span className="ts">⚠ stale</span> config flag renamed — re-verify before use
      </div>
      <div className="receiptFoot">
        signed · checkpoint <span className="tv">a1b9f3c2</span> · verify offline: <code>daimon verify</code>
      </div>
    </div>
  );
}
