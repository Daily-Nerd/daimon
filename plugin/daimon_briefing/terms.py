"""Term extraction for recall and carry: prompt -> salient retrieval terms.

Pure text functions with no daimon imports, so `view` (which `carry` and
`recall` both sit under) can be imported by either without a cycle. Moved out
of `recall` in #1132 PR 9a; `recall` re-exports the three public names for one
release.
"""

import re
import unicodedata

# Words that carry no retrieval signal in a work prompt: English function words
# plus the request-noise vocabulary of talking to an agent. Salience = what's
# LEFT after these; a prompt reduced to nothing stays silent.
_STOPWORDS = frozenset("""
a about after again all also and any are because been before being but can
cant come could did didnt does doesnt doing dont down each few for from had
has have having her here him his how into its itself just let lets like make
more most much must new not now off once only other our out over own same
she should side some still such than that the their them then there these
they this those through too under until very was way well were what when
where which while who why will with would you your yours
please help want need fix add use using used code file files run running
work working thing things stuff issue problem question trying still
algo antes aqui asi aun bien cada casi como con cual cuando del desde donde
ella ellos entre era ese esa eso esta estas este esto estos hace hacer hacia
hasta hay las les los mas menos mientras misma mismo mucho muy nada nos
nosotros otra otro para pero poco por porque pues que quien ser sin sobre
son soy sus tal tambien tanto tener tiene toda todo todos una uno unos
usted vamos
favor ayuda ayudame necesito quiero puedes puedo podes dale arregla arreglar
agrega agregar usa usar usando corre correr corriendo funciona funcionar
codigo archivo archivos cosa cosas problema problemas pregunta preguntas
tratando todavia entonces ahora gracias quizas intenta intentar
""".split())
# Spanish entries are stored diacritic-folded (tambien, not también) because
# salient_terms folds tokens before the stopword check, one entry covers both
# spellings. Both language bands mirror each other: function words plus the
# imperative/filler band (favor/ayuda/necesito = please/help/need); scar #18
# rule, do not drop beyond the frequency band the English list established.

_TERM_CAP = 24          # bounded query cost; 12 dropped real cue terms on long
                        # prompts (#31 item 5, encoding-specificity inversion)
_MIN_TERMS = 2          # a one-word prompt is never a retrieval request


def _fold(tok: str) -> str:
    """Strip combining marks so terms align with what FTS5 stored: the index
    uses unicode61 with its remove_diacritics default, so it holds "sesion"
    for "sesión", folded prompt terms match, raw accents never would."""
    return "".join(
        c for c in unicodedata.normalize("NFD", tok) if not unicodedata.combining(c))


_TOKEN_RE = re.compile(r"\w[\w\-]*")
# Identifier separators + camelCase: `auth_token`, `session-start`, `parseJSON`.
_SUBTOKEN_SPLIT_RE = re.compile(r"[_\-./:]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Cheap pre-check: does this token have any boundary worth splitting on?
_SPLITTABLE_RE = re.compile(r"[_\-./:]|[a-z0-9][A-Z]")
# Longest first so `-ies` is tried before `-s`.
_INFLECTIONS = ("ings", "edly", "ing", "ies", "ers", "est", "ed", "es", "er",
                "ly", "s", "d")
_STEM_MIN = 3   # never stem down to a stub shorter than a salient term


def _match_units(text: str) -> set:
    """Every whole word-unit a salient term may legitimately match (#490).

    Retrieval is FTS5 `MATCH` under unicode61, strict token equality, while
    the gates that judge it (`_MIN_OVERLAP` here, cli's `_STALE_MIN_HITS` via
    `term_hits`) used substring containment. Substring hits are a strict
    superset of token hits, so every threshold stated in "distinct salient
    terms" was evaluated on an inflated statistic, one-sided and always
    permissive: `port` was credited against `transport`, `one` against
    `honest`, `cli` against `client`.

    Raw token equality is the wrong correction. `salient_terms` tokenizes on
    `\\w[\\w-]*`, so compound identifiers are SINGLE tokens and substring
    matching was the only reason a query for `token` reached `auth_token`, in
    a code corpus that is the vocabulary, not noise. Measured on the real
    corpus, only ~14% of substring-only credits were genuine mid-word false
    positives; the rest were compounds (~72%) and inflections (~15%).

    So: split each token on identifier separators and camelCase, add cheap
    inflection stems, and credit a term only when it equals a whole unit. A
    term is never credited for matching the middle of a word.
    """
    units = set()
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0)
        # Fast path: this runs over every candidate row on the per-prompt
        # critical path, and _fold's NFD normalize dominates. Almost every
        # token is plain ASCII with no identifier boundary, so check for both
        # before paying for either.
        low = raw.lower() if raw.isascii() else _fold(raw).lower()
        units.add(low)
        if not _SPLITTABLE_RE.search(raw):
            continue
        for part in _SUBTOKEN_SPLIT_RE.split(_CAMEL_RE.sub("-", raw)):
            if part:
                units.add(part.lower() if part.isascii()
                          else _fold(part).lower())
    return units


def _term_variants(term: str) -> set:
    """Inflected forms of ONE salient term.

    Morphology is folded on the QUERY side, not the haystack side, and that is
    a performance decision with teeth: `suggest` compares <=24 terms against up
    to 256 candidate rows, so expanding the terms once per prompt costs ~24
    small sets while stemming every haystack token costs thousands. Measured on
    real rows, haystack-side stemming ran ~7x slower than the substring
    matching it replaced; this direction is ~1.4x.

    Over-generous in one direction only: a form that is not a real word can be
    generated (`statuss`), which at worst credits a term a stemmer would also
    credit. It never removes a form.
    """
    forms = {term}
    for suf in _INFLECTIONS:
        forms.add(term + suf)
        if term.endswith(suf) and len(term) - len(suf) >= _STEM_MIN:
            base = term[:-len(suf)]
            forms.add(base)
            if suf == "ies":
                forms.add(base + "y")
            elif suf in ("es", "ed", "er", "est", "ing"):
                forms.add(base + "e")
    if term.endswith("y") and len(term) > _STEM_MIN:
        forms.add(term[:-1] + "ies")
    if not term.endswith("e"):
        forms.add(term + "es")
    else:
        forms.add(term + "s")
    return forms


def credited_terms(terms, text: str) -> set:
    """Which of `terms` the text legitimately answers, on word boundaries."""
    units = _match_units(text)
    return {t for t in terms if _term_variants(t) & units}


def salient_terms(prompt: str) -> list[str]:
    """Prompt -> deduped lowercase retrieval terms, prompt order preserved.
    Tokens are word runs (unicode: "sesión" stays one token, never "sesi"+"n";
    code identifiers survive: auth_token stays whole), diacritic-folded to
    match the FTS5 index; <3 chars and stopwords drop. Fewer than _MIN_TERMS
    remaining -> [] (callers stay silent)."""
    out: list[str] = []
    seen = set()
    for m in re.finditer(r"\w[\w\-]*", prompt):
        tok = _fold(m.group(0)).lower()
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= _TERM_CAP:
            break
    return out if len(out) >= _MIN_TERMS else []


# #450: literal openings of the host-emitted blocks that reach the prompt hook
# as if they were user input, background-task notifications, teammate/agent
# messages, slash-command output. Measured on the maintainer's transcripts,
# 37.9% of injections landed on these, at the same rate as on real prompts:
# nothing consumes those suggestions, so they are pure token cost. Literal and
# case-sensitive on purpose, the hosts emit exactly one casing, and loosening
# the match only buys false skips.
_MACHINE_MARKERS = (
    "[SYSTEM NOTIFICATION",
    "<task-notification>",
    "<teammate-message",
    "<agent-message",
    "<local-command-stdout>",
)

_MACHINE_SCAN_CHARS = 400   # opening region only, see is_machine_prompt


def is_machine_prompt(prompt: str) -> bool:
    """True when the prompt is structurally a host-emitted block rather than a
    person asking for work (#450). Deliberately conservative: a missed skip is
    the status quo, a wrong skip costs one suggestion.

    Boundary, a marker counts only when it OPENS A LINE inside the first
    _MACHINE_SCAN_CHARS characters (after leading whitespace):

      - Line-start, because a machine block's marker is the block's opening;
        a human quoting one does it mid-sentence ("why does the hook fire on
        <task-notification> blocks?"). A plain substring scan would silence
        recall on exactly the prompts that discuss recall. Indented markers
        (a pasted code sample) are left ambiguous and still get suggestions.
      - Windowed, because a genuine prompt may paste a whole block far below
        its own question; only the opening region can carry the block that IS
        the prompt. Observed shape: the notification wrapper opens with
        `[SYSTEM NOTIFICATION` at offset 0 and carries `<task-notification>`
        ~490 chars in, past this window, the marker list is redundant for
        that reason, so the window never has to be widened to catch it.

    Truncation at the window can only split a marker, i.e. only ever miss a
    skip, which is the safe direction.
    """
    head = prompt.lstrip()[:_MACHINE_SCAN_CHARS]
    return any(line.startswith(_MACHINE_MARKERS) for line in head.split("\n"))
