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

const PYPI_VERSION = '0.28.0';

const ICON = {
  viewBox: '0 0 24 24',
  width: 20,
  height: 20,
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
  focusable: false,
};

async function copyText(cmd: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(cmd);
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = cmd;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

/** One copy behaviour for both the pill button and the command row. */
function useCopy(cmd: string): [boolean, () => Promise<void>] {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await copyText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable, leave the glyph unchanged */
    }
  };
  return [copied, copy];
}

function copyLabel(copied: boolean, cmd: string): string {
  return copied
    ? translate({id: 'landing.copy.done', message: 'copied'})
    : translate({id: 'landing.copy', message: 'copy'}) + ': ' + cmd;
}

function CopyIcon({done}: {done: boolean}): ReactNode {
  return done ? (
    <svg {...ICON}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  ) : (
    <svg {...ICON}>
      <rect x="9" y="9" width="11" height="11" rx="2.5" />
      <path d="M5 15V5.5A2.5 2.5 0 0 1 7.5 3H15" />
    </svg>
  );
}

/** Accent pill that shows the command and copies it. */
function CopyButton({cmd}: {cmd: string}): ReactNode {
  const [copied, copy] = useCopy(cmd);
  return (
    <button
      type="button"
      className="dmBtn"
      aria-label={copyLabel(copied, cmd)}
      onClick={copy}>
      <span>{cmd}</span>
      <CopyIcon done={copied} />
    </button>
  );
}

function CopyCmd({cmd, label}: {cmd: string; label?: ReactNode}): ReactNode {
  const [copied, copy] = useCopy(cmd);
  return (
    <div className="cmdStep">
      {label ? <div className="cmdLabel">{label}</div> : null}
      <div className="cmdLine">
        <code>{cmd}</code>
        <button
          type="button"
          className="copyBtn"
          aria-label={copyLabel(copied, cmd)}
          onClick={copy}>
          <CopyIcon done={copied} />
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
      <Link className="dmLink" to="/docs/">
        {translate({id: 'landing.close.docs', message: 'Read the docs'})}
      </Link>
    </div>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout description={siteConfig.tagline}>
      <header className="dmHero">
        <div className="dmContainer">
          <div className="dmHeroGrid">
            <div className="dmHeroText">
              <h1>
                {translate({
                  id: 'landing.hero.title',
                  message: 'Session memory for coding agents',
                })}
              </h1>
              <p className="dmLead">
                {translate({
                  id: 'landing.hero.lead',
                  message: 'daimon saves what was decided and what is still open.',
                })}
              </p>
              <div className="dmActions">
                <CopyButton cmd="uv tool install daimon-briefing" />
                <Link className="dmLink" to="/docs/">
                  {translate({id: 'landing.close.docs', message: 'Read the docs'})}
                </Link>
              </div>
              <p className="dmNot">
                {translate({
                  id: 'landing.hero.not',
                  message:
                    'It is not a live monitor for background agents. It does not learn how-to skills or pass fixes from one agent to another.',
                })}
              </p>
            </div>
            <HomeReceipt />
          </div>
        </div>
      </header>
      <main>
        <section className="dmSection dmProblem">
          <div className="dmContainer">
            <div className="dmProblemGrid">
              <h2 className="dmStatement">
                {translate({
                  id: 'landing.hero.pain',
                  message:
                    'Your agent forgets every session, then asserts what it half-remembers.',
                })}
              </h2>
              <p className="dmLead">
                {translate({
                  id: 'landing.problem.lead',
                  message:
                    'When the next one starts, your agent gets a short briefing, with each item marked as an exact quote or an inference.',
                })}
              </p>
            </div>
          </div>
        </section>
        <section className="dmSection dmBand dmInstall">
          <div className="dmContainer dmSplit">
            <div>
              <h2>
                {translate({
                  id: 'landing.install.title',
                  message: 'Install it once and hooks do the rest',
                })}
              </h2>
              <p className="dmLead" style={{marginTop: 'var(--space-lg)'}}>
                {translate({
                  id: 'landing.install.lead',
                  message: 'In normal use there are no commands to run.',
                })}
              </p>
            </div>
            <InstallBlock />
          </div>
        </section>
        <section className="dmSection dmVerify">
          <div className="dmContainer">
            <div className="dmSectionHead">
              <h2>
                {translate({
                  id: 'landing.verify.title',
                  message: 'A checkpoint verifying itself',
                })}
              </h2>
              <p className="dmLead">
                {translate({
                  id: 'landing.anatomy.claim',
                  message:
                    'A checkpoint is the signed record a session leaves behind, and every line in it earns its class.',
                })}
              </p>
            </div>
            <VerifyReplay />
            <div className="dmVerifyNotes">
              <p className="claim">
                {translate({
                  id: 'landing.verify.claim',
                  message:
                    'Every claim is re-checked against the transcript before it reaches your agent.',
                })}
              </p>
              <p className="scope">
                {translate({
                  id: 'landing.verify.scope',
                  message:
                    'Verified means daimon found this exact quote in your transcript, matched after folding case, whitespace, and formatting, with elided spans allowed. It is a mechanical check, not a truth claim.',
                })}
              </p>
            </div>
          </div>
        </section>
        <section className="dmSection dmBand dmAnatomy">
          <div className="dmContainer">
            <div className="dmSectionHead">
              <h2>
                {translate({
                  id: 'landing.anatomy.title',
                  message: 'How an item proves itself',
                })}
              </h2>
            </div>
            <div className="anatomyPair">
              <div className="anatomyCard">
                <div className="anatomyWhen">at write time</div>
                <div className="anatomyLine">
                  <span className="tv">
                    <span aria-hidden="true">✔ </span>verbatim
                  </span>{' '}
                  "port is 8080"
                </div>
                <div className="anatomySub">
                  <span aria-hidden="true">└ </span>transcript line 87 · sig 9f3c…a41b
                </div>
              </div>
              <div className="anatomyArrow" aria-hidden="true">
                →
              </div>
              <div className="anatomyCard">
                <div className="anatomyWhen">21 days later</div>
                <div className="anatomyLine">
                  <span className="ti">
                    <span aria-hidden="true">~ </span>inferred
                  </span>{' '}
                  "port is 8080"
                </div>
                <div className="anatomySub">
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
        <section className="dmSection dmClose">
          <div className="dmContainer">
            <h2 className="dmStatement">
              {translate({
                id: 'landing.close.conviction',
                message:
                  'Every item above is checkable offline, without trusting us, the model, or this page.',
              })}
            </h2>
            <div className="dmActions">
              <CopyButton cmd="uv tool install daimon-briefing" />
            </div>
            <ul className="dmFacts">
              <li>
                <a href="https://github.com/Daily-Nerd/daimon/blob/main/LICENSE">
                  Apache-2.0
                </a>
              </li>
              <li>
                {translate({id: 'landing.close.deps', message: 'Python stdlib only'})}
              </li>
              <li>
                {translate({id: 'landing.close.sync', message: 'team sync over git'})}
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
          </div>
        </section>
      </main>
    </Layout>
  );
}
