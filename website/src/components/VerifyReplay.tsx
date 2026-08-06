import {useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';

const LINES: ReactNode[] = [
  <span key="0" className="replayDim">$ daimon checkpoint · verifying 3 claims…</span>,
  <span key="1">"retry cap is 30s" <span className="replayDim">→ transcript L214</span> <span className="tv">✔ verbatim</span></span>,
  <span key="2">"port is 8080" <span className="replayDim">→ no match</span> <span className="tx">✘</span> <span className="ti">downgraded to ~ inferred</span></span>,
  <span key="3">"flag renamed in cfg" <span className="replayDim">→ anchor moved</span> <span className="ts">⚠ flagged stale</span></span>,
  <span key="4" className="replayDim">signed a1b9f3c2 · 1 verified, 1 downgraded, 1 flagged</span>,
];

export default function VerifyReplay(): ReactNode {
  const [shown, setShown] = useState(LINES.length);
  const [played, setPlayed] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
    const el = ref.current;
    if (!el) return undefined;
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        obs.disconnect();
        setShown(0);
        let i = 0;
        const t = setInterval(() => {
          i += 1;
          setShown(i);
          if (i >= LINES.length) { clearInterval(t); timer.current = null; setPlayed(true); }
        }, 700);
        timer.current = t;
      }
    }, {threshold: 0.5});
    obs.observe(el);
    return () => {
      obs.disconnect();
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  return (
    <div className="replay" ref={ref} data-played={played}>
      {LINES.map((l, i) => (
        <div key={i} className={i < shown ? 'replayLine' : 'replayLine replayHidden'}>{l}</div>
      ))}
    </div>
  );
}
