import {useEffect, useState} from 'react';
import type {ReactNode} from 'react';

export default function HomeReceipt(): ReactNode {
  const [checked, setChecked] = useState(true);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
    setChecked(false);
    const t = setTimeout(() => setChecked(true), 900);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="receipt">
      <div className="receiptHead">
        <span>DAIMON BRIEFING</span> <span>session end · 21:14 UTC</span>
      </div>
      <div className="receiptRow">
        <span className="tv">
          <span aria-hidden="true">✔ </span>verbatim
        </span>{' '}
        "retry uses exponential backoff, cap 30s"
      </div>
      <div className="receiptSub">
        <span aria-hidden="true">└ </span>transcript line 214{' '}
        <span className={checked ? 'chip chipOk' : 'chip chipPending'}>
          {checked ? (
            <>
              <span aria-hidden="true">✔ </span>checked
            </>
          ) : (
            'checking…'
          )}
        </span>
      </div>
      <div className="receiptRow">
        <span className="ti">
          <span aria-hidden="true">~ </span>inferred
        </span>{' '}
        cache layer is the bottleneck
      </div>
      <div className="receiptRow">
        <span className="ts">
          <span aria-hidden="true">⚠ </span>stale
        </span>{' '}
        config flag renamed — re-verify before use
      </div>
      <div className="receiptFoot">
        signed ed25519 · sig 9f3c…a41b · key daimon-team · verify:{' '}
        <code>daimon verify</code>
      </div>
    </div>
  );
}
