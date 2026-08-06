import {translate} from '@docusaurus/Translate';
import {useCallback, useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';

const LINES: ReactNode[] = [
  <span key="0" className="replayHead">
    $ daimon checkpoint · verifying 23 claims…
  </span>,
  <span key="1">
    "retry cap is 30s" <span aria-hidden="true">→</span> transcript L214{' '}
    <span className="tv">
      <span aria-hidden="true">✔ </span>verbatim
    </span>
  </span>,
  <span key="2">
    "port is 8080" <span aria-hidden="true">→</span> no match{' '}
    <span aria-hidden="true">→</span>{' '}
    <span className="ti">
      downgraded to <span aria-hidden="true">~ </span>inferred
    </span>
  </span>,
  <span key="3">
    "flag renamed in cfg" <span aria-hidden="true">→</span> the code it quoted
    changed{' '}
    <span className="ts">
      <span aria-hidden="true">⚠ </span>flagged stale
    </span>
  </span>,
  <span key="4">signed 7e4c02d1 · 19 verified, 3 downgraded, 1 flagged</span>,
];

export default function VerifyReplay(): ReactNode {
  const [shown, setShown] = useState(LINES.length);
  const [played, setPlayed] = useState(true);
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const run = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    setPlayed(false);
    setShown(0);
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      setShown(i);
      if (i >= LINES.length) {
        clearInterval(t);
        timer.current = null;
        setPlayed(true);
      }
    }, 350);
    timer.current = t;
  }, []);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
    const el = ref.current;
    if (!el) return undefined;
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          obs.disconnect();
          run();
        }
      },
      {threshold: 0.25},
    );
    obs.observe(el);
    return () => {
      obs.disconnect();
      if (timer.current) clearInterval(timer.current);
    };
  }, [run]);

  return (
    <div className="replay" ref={ref} data-played={played}>
      <button
        type="button"
        className="replayBtn"
        aria-label={translate({
          id: 'landing.replay.aria',
          message: 'Replay the verification',
        })}
        onClick={run}>
        {translate({id: 'landing.replay.btn', message: 'replay'})}
      </button>
      <div className="replayLines">
        {LINES.map((l, i) => (
          <div key={i} className={i < shown ? 'replayLine' : 'replayLine replayHidden'}>
            {l}
          </div>
        ))}
      </div>
    </div>
  );
}
