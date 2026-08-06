import Link from '@docusaurus/Link';
import {translate} from '@docusaurus/Translate';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import {useState} from 'react';
import type {ReactNode} from 'react';
import HomeReceipt from '@site/src/components/HomeReceipt';
import VerifyReplay from '@site/src/components/VerifyReplay';

const HOSTS = [
  {id: 'claude', label: 'Claude Code'},
  {id: 'codex', label: 'Codex'},
  {id: 'gemini', label: 'Gemini CLI'},
  {id: 'windsurf', label: 'Windsurf'},
];

const PYPI_VERSION = '0.26.0';

function CopyCmd({cmd}: {cmd: string}): ReactNode {
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
      <code>{cmd}</code>
      <button
        type="button"
        className="copyBtn"
        aria-label={'copy: ' + cmd}
        onClick={copy}>
        {copied
          ? translate({id: 'landing.copy.done', message: 'copied'})
          : translate({id: 'landing.copy', message: 'copy'})}
      </button>
    </div>
  );
}

function InstallBlock(): ReactNode {
  const [host, setHost] = useState(HOSTS[0]);
  return (
    <div className="install">
      <div
        className="hostChips"
        role="group"
        aria-label={translate({
          id: 'landing.install.hosts',
          message: 'Choose your agent host',
        })}>
        {HOSTS.map((h) => (
          <button
            key={h.id}
            type="button"
            className={h.id === host.id ? 'hostChip hostChipOn' : 'hostChip'}
            aria-pressed={h.id === host.id}
            onClick={() => setHost(h)}>
            {h.label}
          </button>
        ))}
      </div>
      <div className="installBlock">
        <CopyCmd cmd="uv tool install daimon-briefing" />
        <CopyCmd cmd={`daimon hooks install ${host.id}`} />
      </div>
      <Link className="quietLink" to="/docs/">
        {translate({id: 'landing.close.docs', message: 'Read the docs'})}
      </Link>
    </div>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout description={siteConfig.tagline}>
      <header className="hero--daimon text--center">
        <div className="bandInner bandInner--hero">
          <p className="heroEyebrow">
            <span className="heroMark">daimon</span>
            <span aria-hidden="true"> · </span>
            <span>
              {translate({id: 'landing.hero.eyebrow', message: 'open-source CLI'})}
            </span>
          </p>
          <h1>
            {translate({
              id: 'landing.hero.title',
              message: "Your agent's memory, with receipts you can check",
            })}
          </h1>
          <p className="subtitle">
            {translate({
              id: 'landing.hero.sub',
              message:
                'Other memory tools let the model grade its own homework. daimon shows you the transcript line, the exact quote, and a signature you can check offline.',
            })}
          </p>
          <InstallBlock />
          <HomeReceipt />
        </div>
      </header>
      <main>
        <section className="sectionBand bandReplay">
          <div className="bandInner bandInner--replay">
            <h2 className="sectionTitle">
              {translate({
                id: 'landing.verify.title',
                message: 'A checkpoint verifying itself',
              })}
            </h2>
            <VerifyReplay />
          </div>
        </section>
        <section className="sectionBand bandAnatomy">
          <div className="bandInner bandInner--anatomy">
            <h2 className="sectionTitle">
              {translate({
                id: 'landing.anatomy.title',
                message: 'How an item proves itself',
              })}
            </h2>
            <div className="anatomyCode">
              <div className="anatomyLine">
                <span className="ti">
                  <span aria-hidden="true">~ </span>inferred
                </span>{' '}
                "port is 8080"
              </div>
              <div className="anatomyLine anatomySub">
                <span aria-hidden="true">└ </span>was{' '}
                <span aria-hidden="true">✔ </span>verbatim · quote no longer
                matches the transcript · downgraded automatically
              </div>
            </div>
            <dl className="defs">
              <div className="defRow">
                <dt>{translate({id: 'landing.anatomy.class.term', message: 'class'})}</dt>
                <dd>
                  {translate({
                    id: 'landing.anatomy.class',
                    message: '— earned by checking, never self-declared',
                  })}
                </dd>
              </div>
              <div className="defRow">
                <dt>
                  {translate({id: 'landing.anatomy.words.term', message: 'exact words'})}
                </dt>
                <dd>
                  {translate({
                    id: 'landing.anatomy.words',
                    message: '— quoted from the transcript, not paraphrased',
                  })}
                </dd>
              </div>
              <div className="defRow">
                <dt>
                  {translate({id: 'landing.anatomy.receipt.term', message: 'receipt'})}
                </dt>
                <dd>
                  {translate({
                    id: 'landing.anatomy.receipt',
                    message: '— the line number and signature you can look up',
                  })}
                </dd>
              </div>
            </dl>
          </div>
        </section>
        <section className="sectionBand bandClose">
          <div className="bandInner bandInner--close">
            <ul className="factList">
              <li>
                <a href="https://github.com/Daily-Nerd/daimon/blob/main/LICENSE">
                  Apache-2.0
                </a>
              </li>
              <li>
                {translate({
                  id: 'landing.close.deps',
                  message: 'Python stdlib only — zero dependencies',
                })}
              </li>
              <li>
                {translate({
                  id: 'landing.close.sync',
                  message: 'Team sync over a plain git remote',
                })}
              </li>
              <li>
                <a href="https://pypi.org/project/daimon-briefing/">
                  {translate(
                    {id: 'landing.close.pypi', message: 'v{version} on PyPI'},
                    {version: PYPI_VERSION},
                  )}
                </a>
              </li>
            </ul>
            <Link className="quietLink" to="/docs/">
              {translate({id: 'landing.close.docs', message: 'Read the docs'})}
            </Link>
          </div>
        </section>
      </main>
    </Layout>
  );
}
