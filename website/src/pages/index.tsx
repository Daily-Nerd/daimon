import Link from '@docusaurus/Link';
import {translate} from '@docusaurus/Translate';
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

function getFeatures() {
  return [
    {
      title: translate({
        id: 'landing.feature.briefings.title',
        message: 'Briefings, not cold starts',
      }),
      body: translate({
        id: 'landing.feature.briefings.body',
        message:
          'Every session ends in a checkpoint; the next one opens with what you were doing, what was decided, and what is still open.',
      }),
    },
    {
      title: translate({
        id: 'landing.feature.trust.title',
        message: 'Trust classes on every item',
      }),
      body: translate({
        id: 'landing.feature.trust.body',
        message:
          'Verbatim items carry exact quotes verified against the transcript at serialize time. Inferred items say so. Stale carries get flagged for re-verification.',
      }),
    },
    {
      title: translate({
        id: 'landing.feature.receipts.title',
        message: 'Receipts you can verify offline',
      }),
      body: translate({
        id: 'landing.feature.receipts.body',
        message:
          'Checkpoints are signed. A third party can check what a session knew, when, without trusting the machine that wrote it.',
      }),
    },
    {
      title: translate({
        id: 'landing.feature.team.title',
        message: 'Team memory over git',
      }),
      body: translate({
        id: 'landing.feature.team.body',
        message:
          'Checkpoints sync through a plain git remote with a default-closed scope allowlist. No server, no accounts.',
      }),
    },
    {
      title: translate({
        id: 'landing.feature.hosts.title',
        message: 'Works with your host',
      }),
      body: translate({
        id: 'landing.feature.hosts.body',
        message:
          'Claude Code, Codex, Gemini CLI, and Windsurf — installed as hooks, removed as easily.',
      }),
    },
    {
      title: translate({
        id: 'landing.feature.local.title',
        message: 'Local and zero-dependency',
      }),
      body: translate({
        id: 'landing.feature.local.body',
        message:
          'Python stdlib only. Your transcripts never leave your machine unless you configure a team remote.',
      }),
    },
  ];
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  const features = getFeatures();
  return (
    <Layout description={siteConfig.tagline}>
      <header className="hero--daimon text--center">
        <h1>{siteConfig.title}</h1>
        <p className="subtitle">
          {translate({
            id: 'landing.subtitle',
            message:
              'Session memory your coding agents can prove. Briefings with trust classes, quotes verified against the transcript, and signed receipts.',
          })}
        </p>
        <div className="install">
          <InstallBlock />
        </div>
        <div>
          <Link className="button button--primary" to="/docs/">
            {translate({id: 'landing.cta.start', message: 'Get started'})}
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
          {translate({
            id: 'landing.quote',
            message:
              'Stop re-explaining yourself every session. The next session opens already knowing what you were doing, what you decided, and what is still open — with the evidence to check it.',
          })}
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
