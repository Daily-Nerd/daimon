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

const PYPI_VERSION = '0.27.0';

const ICON = {
  viewBox: '0 0 24 24',
  width: 18,
  height: 18,
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
  focusable: false,
};

function CopyCmd({cmd, label}: {cmd: string; label?: ReactNode}): ReactNode {
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
      /* clipboard unavailable — leave the glyph unchanged */
    }
  };
  return (
    <div className="cmdStep">
      {label ? <div className="cmdLabel">{label}</div> : null}
      <div className="cmdLine">
        <code>{cmd}</code>
        <button
          type="button"
          className={copied ? 'copyBtn copyBtnDone' : 'copyBtn'}
          aria-label={
            copied
              ? translate({id: 'landing.copy.done', message: 'copied'})
              : translate({id: 'landing.copy', message: 'copy'}) + ': ' + cmd
          }
          onClick={copy}>
          {copied ? (
            <svg {...ICON}>
              <path d="M20 6 9 17l-5-5" />
            </svg>
          ) : (
            <svg {...ICON}>
              <rect x="9" y="9" width="11" height="11" rx="2.5" />
              <path d="M5 15V5.5A2.5 2.5 0 0 1 7.5 3H15" />
            </svg>
          )}
        </button>
      </div>
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
        <CopyCmd
          cmd="uv tool install daimon-briefing"
          label={translate({
            id: 'landing.install.step1',
            message: '1 · install the tool',
          })}
        />
        <CopyCmd
          cmd={`daimon hooks install ${host.id}`}
          label={translate(
            {id: 'landing.install.step2', message: '2 · connect it to {host}'},
            {host: host.label},
          )}
        />
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
              message: 'Memory anyone can verify',
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
            <div className="replayIntro">
              <h2 className="sectionTitle">
                {translate({
                  id: 'landing.verify.title',
                  message: 'A checkpoint verifying itself',
                })}
              </h2>
              <p className="claimLine">
                {translate({
                  id: 'landing.anatomy.claim',
                  message:
                    'A checkpoint is the signed record a session leaves behind — every line in it earns its class.',
                })}
              </p>
              <p className="claimSub">
                {translate({
                  id: 'landing.verify.claim',
                  message:
                    'Every claim is re-checked against the transcript before it reaches your agent.',
                })}
              </p>
            </div>
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
            <div className="anatomyPair">
              <div className="anatomyCard">
                <div className="anatomyLine anatomyWhen">at write time</div>
                <div className="anatomyLine">
                  <span className="tv">
                    <span aria-hidden="true">✔ </span>verbatim
                  </span>{' '}
                  "port is 8080"
                </div>
                <div className="anatomyLine anatomySub">
                  <span aria-hidden="true">└ </span>transcript line 87 · sig 9f3c…a41b
                </div>
              </div>
              <div className="anatomyArrow" aria-hidden="true">
                →
              </div>
              <div className="anatomyCard">
                <div className="anatomyLine anatomyWhen">21 days later</div>
                <div className="anatomyLine">
                  <span className="ti">
                    <span aria-hidden="true">~ </span>inferred
                  </span>{' '}
                  "port is 8080"
                </div>
                <div className="anatomyLine anatomySub">
                  <span aria-hidden="true">└ </span>quote no longer matches · downgraded
                  automatically
                </div>
              </div>
            </div>
            <dl className="anatomyDefs">
                <div className="anatomyDefRow">
                  <dt>
                    {translate({id: 'landing.anatomy.class.term', message: 'class'})}
                  </dt>
                  <dd>
                    {translate({
                      id: 'landing.anatomy.class',
                      message: 'earned by checking, never self-declared',
                    })}
                  </dd>
                </div>
                <div className="anatomyDefRow">
                  <dt>
                    {translate({id: 'landing.anatomy.words.term', message: 'exact words'})}
                  </dt>
                  <dd>
                    {translate({
                      id: 'landing.anatomy.words',
                      message: 'quoted from the transcript, not paraphrased',
                    })}
                  </dd>
                </div>
                <div className="anatomyDefRow">
                  <dt>
                    {translate({id: 'landing.anatomy.receipt.term', message: 'receipt'})}
                  </dt>
                  <dd>
                    {translate({
                      id: 'landing.anatomy.receipt',
                      message: 'the line number and signature you can look up',
                    })}
                  </dd>
                </div>
              </dl>
          </div>
        </section>
        <section className="sectionBand bandClose">
          <div className="bandInner bandInner--close">
            <p className="convictionLine">
              {translate({
                id: 'landing.close.conviction',
                message:
                  'Every item above is checkable offline — without trusting us, the model, or this page.',
              })}
            </p>
            <div className="closeInstall">
              <CopyCmd cmd="uv tool install daimon-briefing" />
            </div>
            <p className="factRow">
              <a
                className="factLink"
                href="https://github.com/Daily-Nerd/daimon/blob/main/LICENSE">
                Apache-2.0
              </a>
              <span aria-hidden="true"> · </span>
              {translate({id: 'landing.close.deps', message: 'Python stdlib only'})}
              <span aria-hidden="true"> · </span>
              {translate({id: 'landing.close.sync', message: 'team sync over git'})}
              <span aria-hidden="true"> · </span>
              <a className="factLink" href="https://pypi.org/project/daimon-briefing/">
                {translate(
                  {id: 'landing.close.pypi', message: 'v{version} on PyPI'},
                  {version: PYPI_VERSION},
                )}
              </a>
            </p>
          </div>
        </section>
      </main>
    </Layout>
  );
}
