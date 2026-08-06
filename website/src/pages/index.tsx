import Link from '@docusaurus/Link';
import {translate} from '@docusaurus/Translate';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import {useEffect, useState} from 'react';
import type {ReactNode} from 'react';
import HomeReceipt from '@site/src/components/HomeReceipt';
import VerifyReplay from '@site/src/components/VerifyReplay';

const HOSTS = ['claude', 'codex', 'gemini', 'windsurf'];

function CopyCmd({cmd, children}: {cmd: string; children: ReactNode}): ReactNode {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(cmd);
      } else {
        const ta = document.createElement('textarea');
        ta.value = cmd;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — leave button label unchanged */
    }
  };
  return (
    <div className="cmdLine">
      <code>{children}</code>
      <button
        type="button"
        className="copyBtn"
        onClick={copy}>
        {copied
          ? translate({id: 'landing.copy.done', message: 'copied'})
          : translate({id: 'landing.copy', message: 'copy'})}
      </button>
    </div>
  );
}

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
    <div className="installBlock">
      <CopyCmd cmd="uv tool install daimon-briefing">{'uv tool install daimon-briefing'}</CopyCmd>
      <CopyCmd cmd="daimon hooks install claude">
        {'daimon hooks install '}
        <span className={fading ? 'hostToken hostFade' : 'hostToken'}>
          {HOSTS[i]}
        </span>
      </CopyCmd>
    </div>
  );
}


export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout description={siteConfig.tagline}>
      <header className="hero--daimon text--center">
        <h1>
          {translate({id: 'landing.hero.title', message: 'Memory your agents can prove'})}
        </h1>
        <p className="subtitle">
          {translate({
            id: 'landing.hero.sub',
            message: 'Every briefing item carries its trust class, its quote, and a signature you can check offline.',
          })}
        </p>
        <HomeReceipt />
        <div className="ctaRow">
          <Link className="button button--primary" to="/docs/">
            {translate({id: 'landing.cta.start', message: 'Get started'})}
          </Link>
          <Link className="button button--secondary" href="https://github.com/Daily-Nerd/daimon">
            GitHub
          </Link>
        </div>
      </header>
      <main>
        <section className="sectionBand text--center">
          <h2 className="sectionTitle">
            {translate({id: 'landing.verify.title', message: 'Watch a checkpoint get verified'})}
          </h2>
          <VerifyReplay />
        </section>
        <section className="sectionBand text--center">
          <h2 className="sectionTitle">
            {translate({id: 'landing.anatomy.title', message: 'Every item earns its class'})}
          </h2>
          <div className="anatomy">
            <div className="anatomyItem">
              <span className="tv">✔ verbatim</span> "retry uses exponential backoff, cap 30s"<br />
              <span className="anatomyDim">└ transcript line 214 · checked at write time</span>
            </div>
            <div className="anatomyLegend">
              <div><span className="tv">↑</span> {translate({id: 'landing.anatomy.class', message: 'the class — earned, not self-declared'})}</div>
              <div><span className="tv">↑</span> {translate({id: 'landing.anatomy.words', message: 'the exact words — quoted, not paraphrased'})}</div>
              <div><span className="tv">↑</span> {translate({id: 'landing.anatomy.receipt', message: 'the receipt — where to look it up'})}</div>
            </div>
          </div>
        </section>
        <section className="sectionBand text--center">
          <h2 className="sectionTitle">
            {translate({id: 'landing.quickstart.title', message: 'Two commands'})}
          </h2>
          <InstallBlock />
          <p className="hostRow">Claude Code · Codex · Gemini CLI · Windsurf</p>
        </section>
        <section className="sectionBand text--center">
          <p className="closeLine">
            {translate({id: 'landing.close.line1', message: 'Other memory tools let the model grade its own homework.'})}
            <br />
            <strong className="tv">
              {translate({id: 'landing.close.line2', message: 'daimon derives trust from evidence it can show you.'})}
            </strong>
          </p>
          <p className="closeMeta">
            {translate({id: 'landing.close.meta', message: 'Team sync over a plain git remote · Python stdlib only · Apache-2.0'})}
          </p>
          <Link className="button button--secondary" to="/docs/">
            {translate({id: 'landing.close.docs', message: 'Read the docs'})}
          </Link>
        </section>
      </main>
    </Layout>
  );
}
