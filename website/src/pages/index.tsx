import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import {useEffect, useState} from 'react';
import type {ReactNode} from 'react';

const HOSTS = ['claude', 'codex', 'gemini', 'windsurf'];

function InstallBlock(): ReactNode {
  const [i, setI] = useState(0);
  const [fading, setFading] = useState(false);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return undefined;
    }
    const t = setInterval(() => {
      setFading(true);
      setTimeout(() => {
        setI((n) => (n + 1) % HOSTS.length);
        setFading(false);
      }, 180);
    }, 2200);
    return () => clearInterval(t);
  }, []);
  return (
    <pre className="installBlock">
      <code>
        {'uv tool install daimon-briefing\n'}
        {'daimon hooks install '}
        <span className={fading ? 'hostToken hostFade' : 'hostToken'}>
          {HOSTS[i]}
        </span>
      </code>
    </pre>
  );
}

const features = [
  {
    title: 'Briefings, not cold starts',
    body: 'Every session ends in a checkpoint; the next one opens with what you were doing, what was decided, and what is still open.',
  },
  {
    title: 'Trust classes on every item',
    body: 'Verbatim items carry exact quotes verified against the transcript at serialize time. Inferred items say so. Stale carries get flagged for re-verification.',
  },
  {
    title: 'Receipts you can verify offline',
    body: 'Checkpoints are signed. A third party can check what a session knew, when, without trusting the machine that wrote it.',
  },
  {
    title: 'Team memory over git',
    body: 'Checkpoints sync through a plain git remote with a default-closed scope allowlist. No server, no accounts.',
  },
  {
    title: 'Works with your host',
    body: 'Claude Code, Codex, Gemini CLI, and Windsurf — installed as hooks, removed as easily.',
  },
  {
    title: 'Local and zero-dependency',
    body: 'Python stdlib only. Your transcripts never leave your machine unless you configure a team remote.',
  },
];

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout description={siteConfig.tagline}>
      <header className="hero--daimon text--center">
        <h1>{siteConfig.title}</h1>
        <p className="subtitle">
          Session memory your coding agents can prove. Briefings with trust
          classes, quotes verified against the transcript, and signed receipts.
        </p>
        <div className="install">
          <InstallBlock />
        </div>
        <div>
          <Link className="button button--primary" to="/docs/">
            Get started
          </Link>{' '}
          <Link
            className="button button--secondary"
            href="https://github.com/Daily-Nerd/daimon">
            GitHub
          </Link>
        </div>
      </header>
      <main>
        <p className="quoteRow">
          Stop re-explaining yourself every session. The next session opens
          already knowing what you were doing, what you decided, and what is
          still open — with the evidence to check it.
        </p>
        <div className="featureGrid">
          {features.map((f) => (
            <div className="featureCard" key={f.title}>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </main>
    </Layout>
  );
}
