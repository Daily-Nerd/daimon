"""
Context-as-Program Benchmark Dataset Generator

Generates 50 synthetic long conversations covering diverse domains and lengths,
rich with signal for CSL (Context Script Language) extraction.

Usage:
    from benchmark.datasets import generate_dataset, load_dataset
    generate_dataset("benchmark/data/conversations.jsonl")
    data = load_dataset("benchmark/data/conversations.jsonl")
"""

import json
import random
import re
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Callable

# ---------------------------------------------------------------------------
# Token estimator (approximate; 1 token ~ 4 chars for English prose)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token on average for English text."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Random data generators for realism
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Quinn", "Avery",
    "Sam", "Jamie", "Dakota", "Reese", "Skyler", "Drew", "Cameron", "Sage",
    "Kai", "Elara", "Orion", "Nova", "Zara", "Ivan", "Lena", "Marcus", "Yuki",
    "Priya", "Diego", "Ingrid", "Sven", "Amara", "Leo", "Maya", "Raj", "Elena",
    "Theo", "Fatima", "Hugo", "Mei", "Kofi", "Anya", "Nadia", "Oscar", "Lila",
    "Viktor", "Sofia", "Kenji", "Isolde", "Ravi", "Talia", "Bruno"
]

LAST_NAMES = [
    "Chen", "Patel", "Okafor", "Lindqvist", "Nakamura", "Rossi", "Bakshi",
    "Petrov", "Silva", "Kim", "O'Brien", "Fischer", "Dubois", "Santos",
    "Kowalski", "Yilmaz", "Andersson", "Cohen", "Murphy", "Singh", "Tanaka",
    "Garcia", "Jensen", "Ali", "Schneider", "Brown", "Watanabe", "Popov",
    "Reyes", "Mueller", "Khan", "Dubois", "Suzuki", "Novak", "Ortiz", "Liu",
    "Kumar", "Smith", "Johnson", "Williams", "Jones", "Davis", "Miller",
    "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White"
]

COMPANIES = [
    "Nebula Systems", "Vertex Labs", "Aether Dynamics", "Orbitware", "Catalyst AI",
    "Prism Health", "Meridian Bio", "Horizon Legal", "Titan Infrastructure",
    "EchoStream", "Flux Analytics", "Kinetic Ventures", "Luminary Media",
    "Nova Robotics", "Pinnacle Data", "Quantum Bridge", "Radiant Energy",
    "Synapse Medical", "TerraForm Industries", "Uplink Security",
    "Vantage Capital", "Warp Drive Dev", "Xenon Pharma", "Yield Finance",
    "Zenith Cloud", "Axon Therapeutics", "BrightPath EdTech", "Cipher Law",
    "DeepCurrent", "Ember Studios"
]

PROJECT_NAMES = [
    "Project Atlas", "Project Mercury", "Project Horizon", "Project Sentinel",
    "Project Chimera", "Project Orion", "Project Vortex", "Project Echo",
    "Project Nexus", "Project Flux", "Project Aegis", "Project Solstice",
    "Project Nebula", "Project Quantum", "Project Resonance", "Project Ember",
    "Project Catalyst", "Project Prism", "Project Zenith", "Project Drift"
]

TECH_STACKS = [
    "Kubernetes + Go + PostgreSQL", "AWS Lambda + Python + DynamoDB",
    "Rust + Kafka + ClickHouse", "Node.js + React + MongoDB",
    "Terraform + GCP + BigQuery", "Elixir + Phoenix + TimescaleDB",
    "Java + Spring + Oracle", "C++ + gRPC + Redis",
    "Flutter + Firebase + TensorFlow Lite", "React Native + GraphQL + AWS"
]

DISEASES = [
    "idiopathic pulmonary fibrosis", "autoimmune hepatitis",
    "chronic lymphocytic leukemia", "Parkinson's disease",
    "systemic lupus erythematosus", "Crohn's disease",
    "amyotrophic lateral sclerosis", "multiple sclerosis",
    "Type 1 diabetes mellitus", "rheumatoid arthritis",
    "glioblastoma multiforme", "narcolepsy with cataplexy",
    "hereditary angioedema", "primary sclerosing cholangitis"
]

MEDICATIONS = [
    "rituximab", "tocilizumab", "nivolumab", "pembrolizumab",
    "etanercept", "adalimumab", "infliximab", "ustekinumab",
    "lenalidomide", "ibrutinib", "osimertinib", "venetoclax",
    "dupilumab", "omalizumab", "benralizumab", "reslizumab",
    "semaglutide", "tirzepatide", "empagliflozin", "dapagliflozin"
]

RESEARCH_FIELDS = [
    "quantum error correction", "CRISPR base editing", "neural radiance fields",
    "topological insulators", "synthetic biology", "causal inference",
    "adversarial robustness", "protein folding", "climate tipping points",
    "gravitational wave astronomy", "langmuir turbulence", "epigenetic clocks"
]

LAW_TERMS = [
    "indemnification clause", "limitation of liability",
    "intellectual property assignment", "non-compete covenant",
    "material adverse change", "right of first refusal",
    "liquidated damages", "force majeure",
    "warranty of merchantability", "governing law provision",
    "arbitration agreement", "confidentiality undertaking"
]

BOOK_GENRES = [
    "speculative fiction", "literary noir", "magical realism",
    "cli-fi", "post-apocalyptic survival", "historical romance",
    "psychological thriller", "epic fantasy", "memoir in essays",
    "cyberpunk", "cozy mystery", "literary fiction"
]


def rand_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def rand_date(start_year: int = 2022, end_year: int = 2025) -> str:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    random_days = random.randint(0, delta.days)
    d = start + timedelta(days=random_days)
    return d.strftime("%Y-%m-%d")


def rand_datetime(start_year: int = 2022, end_year: int = 2025) -> str:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31, 23, 59)
    delta = end - start
    random_seconds = random.randint(0, int(delta.total_seconds()))
    d = start + timedelta(seconds=random_seconds)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def rand_money(min_k: int = 10, max_k: int = 5000) -> str:
    val = random.randint(min_k, max_k)
    if val >= 1000:
        return f"${val // 1000}.{random.randint(0, 99):02d}M"
    return f"${val}K"


def rand_percent() -> str:
    return f"{random.randint(5, 95)}%"


def rand_choice(options: List[str]) -> str:
    return random.choice(options)


def rand_int(lo: int, hi: int) -> int:
    return random.randint(lo, hi)


# ---------------------------------------------------------------------------
# Conversation assembly helpers
# ---------------------------------------------------------------------------

def fmt_turn(speaker: str, text: str) -> str:
    """Format a single dialogue turn."""
    return f"{speaker}: {text}"


def narr(text: str) -> str:
    """Format a narration / stage direction."""
    return f"[{text}]"


def interleave(*speakers_texts: tuple) -> str:
    """Interleave turns from multiple speakers."""
    lines = []
    for speaker, text in speakers_texts:
        lines.append(fmt_turn(speaker, text))
    return "\n\n".join(lines)


def inject_naturalism(conv: str) -> str:
    """Add realistic interruptions, pauses, filler words to a raw conversation."""
    fillers = [
        "Um, ", "Uh, ", "Well, ", "You know, ", "I mean, ", "So, ",
        "Actually, ", "Honestly, ", "Look, ", "Right, ", "Anyway, ",
    ]
    interruptions = [
        "[phone buzzes on table]",
        "[sips coffee]",
        "[shuffles papers]",
        "[pauses, looks out window]",
        "[typing sounds]",
        "[door opens briefly]",
        "[long pause]",
        "[clears throat]",
        "[quiet notification chime]",
        "[leafs through notebook]",
    ]
    # Inject a few fillers at starts of sentences
    sentences = re.split(r'(?<=[.!?])\s+', conv)
    for i in range(len(sentences)):
        if random.random() < 0.08 and sentences[i]:
            sentences[i] = random.choice(fillers) + sentences[i].lstrip()
    conv = " ".join(sentences)

    # Inject a few interruptions
    lines = conv.split("\n")
    for _ in range(random.randint(1, 4)):
        idx = random.randint(0, max(1, len(lines) - 1))
        lines.insert(idx, random.choice(interruptions))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Domain generators
# Each returns a raw conversation string (before naturalism injection).
# ---------------------------------------------------------------------------

# --------------------------
# 1. Software Engineering
# --------------------------

def gen_software_eng(target_tokens: int) -> str:
    names = [rand_name() for _ in range(rand_int(2, 4))]
    company = rand_choice(COMPANIES)
    project = rand_choice(PROJECT_NAMES)
    stack = rand_choice(TECH_STACKS)
    budget = rand_money(50, 2000)
    date1 = rand_date(2023, 2024)
    date2 = rand_date(2024, 2025)
    deadline = rand_date(2025, 2026)
    p0, p1 = names[0], names[1]
    others = names[2:] if len(names) > 2 else []
    # ensure used unconditionally in expansions
    _ = date2, deadline

    turns = []
    turns.append(fmt_turn(p0, f"Hey {p1}, I’ve been looking at the numbers for {project}. The current infrastructure costs are up to {budget} per month, and we’re only serving {rand_int(5, 50)}K daily active users."))
    turns.append(fmt_turn(p1, f"Yeah, I saw the Grafana dashboard this morning. The {stack} stack is eating most of it. {company} can’t keep burning cash like this."))
    turns.append(fmt_turn(p0, f"Exactly. On {date1}, the CFO flagged it. She wants a 30% cut by Q3."))
    turns.append(fmt_turn(p1, f"Thirty percent? That’s aggressive. What’s our runway if we don’t hit it?"))
    turns.append(fmt_turn(p0, f"About {rand_int(6, 14)} months. But here’s the thing — I’ve been running experiments with Rust for the hot path. We could drop latency by {rand_int(20, 60)}% and cut compute by half."))
    turns.append(fmt_turn(p1, f"Rust? Last time we discussed this you were skeptical. What changed?"))
    turns.append(fmt_turn(p0, f"I spent a weekend rewriting the ingestion service. {rand_int(800, 3000)} lines. The memory footprint went from 4GB to 180MB."))
    turns.append(fmt_turn(p1, f"Wow. Okay. But migration cost?"))
    turns.append(fmt_turn(p0, f"I estimated {rand_int(2, 5)} engineer-months. The blocker is team skill gap — only {rand_int(1, 3)} people know Rust."))
    if others:
        turns.append(fmt_turn(others[0], f"I’m one of them. I can lead the migration if we get sign-off by {deadline}."))
    turns.append(fmt_turn(p1, f"Alright. Let’s draft a proposal. I want three options: aggressive rewrite, conservative optimization, and status quo with cloud discounts."))
    turns.append(fmt_turn(p0, f"I’ll have the doc ready by {date2}. One concern though — the VP of Eng prefers Go for everything. He’ll push back."))
    turns.append(fmt_turn(p1, f"I know. I’ll handle him. Just make sure the numbers are bulletproof."))

    # Expand for medium/long
    if target_tokens >= 5000:
        turns.append(fmt_turn(p0, f"Also, there’s a security audit coming up on {rand_date(2025, 2025)}. The pen-testers found {rand_int(3, 12)} critical CVEs in our dependencies."))
        turns.append(fmt_turn(p1, f"Which ones?"))
        turns.append(fmt_turn(p0, f"Mostly in the legacy auth library. I’ve already patched {rand_int(1, 5)} of them, but the rest require a breaking change to the JWT flow."))
        turns.append(fmt_turn(p1, f"So we’re talking migration + security + cost cut, all in the same quarter?"))
        turns.append(fmt_turn(p0, f"Unfortunately, yes. The audit findings have a hard remediation deadline of {rand_date(2025, 2025)}."))
        turns.append(fmt_turn(p1, f"[sighs] Let’s bring this up at the all-hands. We need buy-in from product too, or we’ll be fighting on three fronts."))
        turns.append(fmt_turn(p0, f"Agreed. By the way, {rand_name()} from SRE mentioned they’re building an internal platform team. Might be worth aligning with them so we don’t duplicate effort."))
        turns.append(fmt_turn(p1, f"Good call. I’ll reach out."))

    if target_tokens >= 15000:
        turns.append(narr("The meeting continues for another hour, diving into architecture diagrams."))
        for i in range(rand_int(8, 15)):
            topic = random.choice([
                (p0, f"I ran a load test at {rand_int(1000, 10000)} RPS. P99 spiked to {rand_int(200, 900)}ms."),
                (p1, f"We should consider sharding by tenant_id. The current monolithic approach won’t scale past {rand_int(50, 200)}K users."),
                (p0, f"The Redis cluster had {rand_int(2, 8)} failover events last week. I think we need Sentinel + persistence."),
                (p1, f"I spoke with {rand_name()} at the vendor. They’re offering a {rand_int(15, 40)}% discount if we commit to 3 years."),
                (p0, f"I’m worried about vendor lock-in. What if we need to migrate in year two?"),
                (p1, f"We’d negotiate an exit clause. But honestly, the switching cost is lower than people think."),
                (p0, f"Let’s not forget the edge deployment. {rand_int(3, 8)} regions need local caching."),
                (p1, f"I’ve been prototyping with Envoy. The config is verbose but the performance is solid."),
                (p0, f"Did you see the post-mortem from {rand_date(2024, 2024)}? The root cause was a stale DNS cache."),
                (p1, f"Yeah. I added a runbook for that. TTL should be under {rand_int(5, 30)} seconds for critical services."),
            ])
            turns.append(fmt_turn(topic[0], topic[1]))
        turns.append(fmt_turn(p1, f"Let’s wrap. Action items: proposal by {date2}, security patches by {deadline}, vendor call by {rand_date(2025, 2025)}."))
        turns.append(fmt_turn(p0, "Got it. I’ll sync the doc in Slack."))

    conv = "\n\n".join(turns)
    return inject_naturalism(conv)


# --------------------------
# 2. Product Management / Startup
# --------------------------

def gen_product_mgmt(target_tokens: int) -> str:
    names = [rand_name() for _ in range(rand_int(3, 5))]
    company = rand_choice(COMPANIES)
    product = f"{company} Platform"
    pivot_idea = random.choice([
        "pivot from B2C to B2B SaaS", "shift to API-first monetization",
        "add AI copilot layer", "expand into European markets",
        "acquire a smaller competitor", "sunset legacy feature set"
    ])
    date1 = rand_date(2023, 2024)
    date2 = rand_date(2024, 2025)
    deadline = rand_date(2025, 2026)
    p0, p1, p2 = names[0], names[1], names[2]
    others = names[3:]
    _ = date2, deadline

    turns = []
    turns.append(fmt_turn(p0, f"Okay team, we need to talk about the trajectory. Our Series A was {rand_money(200, 1500)} in {date1}, and we’re burning {rand_money(30, 150)} a month."))
    turns.append(fmt_turn(p1, f"The runway math is brutal. At this rate, we have {rand_int(8, 16)} months left."))
    turns.append(fmt_turn(p2, f"But our NPS is {rand_int(40, 75)}. Users love the product. The problem is monetization, not product-market fit."))
    turns.append(fmt_turn(p0, f"Exactly. That’s why I want to discuss the {pivot_idea}."))
    turns.append(fmt_turn(p1, f"Whoa. That’s a big swing. What’s the evidence?"))
    turns.append(fmt_turn(p0, f"I interviewed {rand_int(15, 45)} enterprise customers. {rand_int(60, 90)}% said they’d pay {rand_int(500, 5000)} per seat annually if we had SSO and audit logs."))
    turns.append(fmt_turn(p2, f"Enterprise features? Our whole brand is simplicity. Won’t that alienate our core users?"))
    turns.append(fmt_turn(p0, f"It might. But our core users convert at {rand_int(1, 5)}% to paid. Enterprise would convert at {rand_int(15, 40)}%. The math is clear."))
    turns.append(fmt_turn(p1, f"What about the team? We’re {rand_int(8, 25)} people. Enterprise sales requires a completely different muscle."))
    turns.append(fmt_turn(p0, f"I know. I’ve been talking to {rand_name()}, who scaled {rand_choice(COMPANIES)} from zero to {rand_money(5000, 50000)} ARR. She’s open to advising."))
    turns.append(fmt_turn(p2, f"Advising isn’t hiring. We’d need at least {rand_int(2, 5)} experienced AEs and a customer success function."))
    turns.append(fmt_turn(p0, f"Correct. I’ve modeled it. The pivot adds {rand_money(50, 300)} in monthly burn for the first {rand_int(6, 12)} months, then turns profitable by {deadline if 'deadline' in dir() else rand_date(2025, 2026)}."))

    if target_tokens >= 5000:
        turns.append(fmt_turn(p1, f"I’m not opposed, but I want to test it. Can we run a closed beta with {rand_int(3, 10)} design partners?"))
        turns.append(fmt_turn(p0, f"That’s the plan. I’ve already soft-committed with {rand_name()} at {rand_choice(COMPANIES)} and {rand_name()} at {rand_choice(COMPANIES)}."))
        turns.append(fmt_turn(p2, f"What about the board? {rand_name()} from the lead investor is pretty conservative."))
        turns.append(fmt_turn(p0, f"I’m presenting on {date2}. I need you both aligned before then. If we go in divided, the board will hesitate."))
        turns.append(fmt_turn(p1, f"I’ll support it if we have a rollback plan. If the beta doesn’t hit {rand_int(3, 8)} paying customers by {rand_date(2025, 2025)}, we revert."))
        turns.append(fmt_turn(p2, f"Same here. Also, I want to keep the freemium tier. It’s our top-of-funnel."))
        turns.append(fmt_turn(p0, f"Absolutely. The pivot is additive, not replacement."))

    if target_tokens >= 15000:
        turns.append(narr("The whiteboard fills with projections and timelines."))
        for i in range(rand_int(10, 18)):
            topic = random.choice([
                (p0, f"Competitor {rand_choice(COMPANIES)} just raised {rand_money(1000, 10000)}. We need to move fast."),
                (p1, f"Their product is inferior but their GTM machine is polished."),
                (p2, f"Our churn rate is {rand_int(2, 8)}% monthly. Industry average is {rand_int(4, 12)}%."),
                (p0, f"I want to hire a head of sales by {rand_date(2025, 2025)}. Budget is {rand_money(120, 250)} base + commission."),
                (p1, f"The cap table is getting tight. Next round will be dilutive."),
                (p2, f"If we hit {rand_money(500, 2000)} ARR by {rand_date(2025, 2025)}, we can raise at a {rand_int(2, 5)}x valuation bump."),
                (p0, f"Our CAC is ${rand_int(50, 300)}. LTV is ${rand_int(800, 3000)}. The ratio is healthy."),
                (p1, f"But CAC in enterprise will be {rand_int(3, 8)}x higher initially."),
                (p2, f"True, but LTV in enterprise is {rand_int(5, 15)}x higher too."),
                (p0, f"We should also consider a vertical focus. Healthcare and fintech have the budget."),
                (p1, f"{rand_name()} from our advisory board suggested legal tech. High willingness to pay."),
                (p2, f"I ran a quick survey. {rand_int(30, 60)}% of legal firms said pricing transparency matters most."),
            ])
            turns.append(fmt_turn(topic[0], topic[1]))
        turns.append(fmt_turn(p0, f"Decision: greenlight the beta. {p1}, you own the pilot program. {p2}, keep the consumer engine running. Next check-in: {date2}."))
        for o in others:
            turns.append(fmt_turn(o, f"I’ll support with {random.choice(['analytics', 'design', 'ops', 'recruiting', 'finance'])}. Let me know what you need."))

    conv = "\n\n".join(turns)
    return inject_naturalism(conv)


# --------------------------
# 3. Scientific Research
# --------------------------

def gen_science(target_tokens: int) -> str:
    names = [rand_name() for _ in range(rand_int(3, 5))]
    field = rand_choice(RESEARCH_FIELDS)
    institution = random.choice([
        "MIT", "Stanford", "ETH Zurich", "Imperial College", "Tsinghua",
        "Max Planck Institute", "CNRS", "Weizmann Institute", "Caltech",
        "University of Tokyo", "CERN", "NIH", "Broad Institute"
    ])
    paper_title = f"Novel approaches to {field} under noisy conditions"
    date1 = rand_date(2023, 2024)
    deadline = rand_date(2025, 2025)
    p0, p1, p2 = names[0], names[1], names[2]

    turns = []
    turns.append(fmt_turn(p0, f"{p1}, I’ve gone through the latest batch of results from the {field} experiments. The signal-to-noise ratio is worse than expected."))
    turns.append(fmt_turn(p1, f"How much worse? We calibrated the setup in {date1}."))
    turns.append(fmt_turn(p0, f"By a factor of {rand_int(3, 10)}x. The baseline drift is {rand_int(12, 40)}% over {rand_int(4, 24)} hours."))
    turns.append(fmt_turn(p2, f"That explains why the replication at {institution} failed. They couldn’t reproduce our p-values."))
    turns.append(fmt_turn(p0, f"Exactly. I think the issue is environmental, not methodological. The humidity in our lab fluctuates between {rand_int(30, 50)}% and {rand_int(60, 90)}%."))
    turns.append(fmt_turn(p1, f"The grant deadline is {deadline}. If we can’t fix this in {rand_int(2, 6)} weeks, we’ll have to submit preliminary data."))
    turns.append(fmt_turn(p2, f"Preliminary data won’t get us the {rand_money(200, 2000)} we need for the next phase."))
    turns.append(fmt_turn(p0, f"I have a proposal. {rand_name()} at {rand_choice(['MIT', 'Oxford', 'Berkeley'])} published a denoising protocol last month. It uses an auxiliary {random.choice(['laser', 'sensor', 'feedback loop', 'calibration standard'])}."))
    turns.append(fmt_turn(p1, f"I saw that. {random.choice(['Nature', 'Science', 'Physical Review Letters', 'Cell', 'NeurIPS'])} {rand_int(2023, 2024)}. But it requires {random.choice(['cryogenic cooling', 'vacuum chamber retrofit', 'custom FPGA programming', 'radiation shielding'])}."))
    turns.append(fmt_turn(p0, f"We already have the {random.choice(['cooling unit', 'chamber', 'FPGA board', 'shielding'])} from the old {rand_choice(PROJECT_NAMES)}. I checked the inventory yesterday."))
    turns.append(fmt_turn(p2, f"That could work. What’s the timeline?"))
    turns.append(fmt_turn(p0, f"{rand_int(2, 4)} weeks for setup, {rand_int(1, 3)} weeks for data collection. We’d have clean results by {rand_date(2024, 2025)}."))

    if target_tokens >= 5000:
        turns.append(fmt_turn(p1, f"I’m concerned about authorship. If {p0} implements this protocol, does {rand_name()} get co-author credit?"))
        turns.append(fmt_turn(p0, f"It’s a methods citation, not a collaboration. But to be safe, I’ll email them."))
        turns.append(fmt_turn(p2, f"Also, the IRB approval for the next human-subject phase expires on {deadline}. We need to renew."))
        turns.append(fmt_turn(p1, f"I’ll handle the IRB. {p0}, you own the protocol migration. {p2}, can you draft the preliminary paper so we have something ready?"))
        turns.append(fmt_turn(p2, f"I can have an outline by {rand_date(2024, 2025)}. The working title is \"{paper_title}\"."))
        turns.append(fmt_turn(p0, f"Good. One more thing — the conference in {random.choice(['Vienna', 'Singapore', 'Boston', 'Kyoto', 'Barcelona'])} is in {rand_date(2025, 2025)}. If we get clean data, we should submit an abstract."))
        turns.append(fmt_turn(p1, f"Deadline for abstracts?"))
        turns.append(fmt_turn(p0, f"{rand_date(2025, 2025)}. {rand_int(14, 28)} days from now."))

    if target_tokens >= 15000:
        turns.append(narr("The team spends the next two hours whiteboarding the experimental redesign."))
        for i in range(rand_int(10, 18)):
            topic = random.choice([
                (p0, f"The standard error in the control group is {rand_int(5, 20)}% higher than treated."),
                (p1, f"Have we ruled out observer bias? {rand_int(1, 3)} of the grad students knew which group was which."),
                (p2, f"I ran a blinded re-analysis. The effect size drops from Cohen’s d={random.choice(['0.45', '0.62', '0.78', '0.91'])} to {random.choice(['0.31', '0.38', '0.55', '0.67'])}."),
                (p0, f"Still significant at p<{random.choice(['0.01', '0.05', '0.001'])}?"),
                (p2, f"Barely. p={random.choice(['0.042', '0.038', '0.049'])}."),
                (p1, f"We need more power. Sample size of {rand_int(30, 80)} is too small for this effect size."),
                (p0, f"Budget allows for {rand_int(100, 300)} subjects if we cut the imaging budget."),
                (p2, f"I’d rather keep imaging and find a cheaper recruitment channel."),
                (p1, f"{rand_name()} from {institution} offered to share their participant pool."),
                (p0, f"That’s a collaboration, which complicates data ownership."),
                (p2, f"We can draft a DUA. {rand_name()} in our legal office does them fast."),
                (p0, f"The preprint server arXiv has a policy change effective {rand_date(2025, 2025)}. We should review before posting."),
            ])
            turns.append(fmt_turn(topic[0], topic[1]))
        turns.append(fmt_turn(p1, f"Let’s adjourn. Action items assigned. Next lab meeting: {rand_date(2025, 2025)}."))

    conv = "\n\n".join(turns)
    return inject_naturalism(conv)


# --------------------------
# 4. Medical Case Discussion
# --------------------------

def gen_medical(target_tokens: int) -> str:
    names = [rand_name() for _ in range(rand_int(3, 5))]
    disease = rand_choice(DISEASES)
    drug = rand_choice(MEDICATIONS)
    age = rand_int(28, 78)
    hospital = random.choice([
        "Mass General", "Johns Hopkins", "Mayo Clinic", "Cleveland Clinic",
        "UCSF Medical Center", "Mount Sinai", "Karolinska Institute",
        "Charité Berlin", "Toronto General", "Royal Melbourne Hospital"
    ])
    date1 = rand_date(2023, 2024)
    date2 = rand_date(2024, 2025)
    p0, p1, p2 = names[0], names[1], names[2]

    turns = []
    turns.append(fmt_turn(p0, f"Morning team. New consult, {age}-year-old with {disease}. Referred from {hospital} on {date1}."))
    turns.append(fmt_turn(p1, f"What’s the presentation?"))
    turns.append(fmt_turn(p0, f"Progressive symptoms over {rand_int(3, 18)} months. Baseline labs show {random.choice(['elevated creatinine', 'thrombocytopenia', 'elevated LDH', 'abnormal LFTs'])}. Imaging shows {random.choice(['bilateral infiltrates', 'hepatosplenomegaly', 'lymphadenopathy', 'ground-glass opacities'])}."))
    turns.append(fmt_turn(p2, f"Has {drug} been tried?"))
    turns.append(fmt_turn(p0, f"Yes, {rand_int(2, 6)} cycles. Partial response, but the patient developed {random.choice(['peripheral neuropathy', 'infusion reactions', 'hepatotoxicity', 'neutropenia'])}."))
    turns.append(fmt_turn(p1, f"Grade?"))
    turns.append(fmt_turn(p0, f"Grade {rand_int(2, 4)}. Dose reduced by {rand_int(25, 50)}%."))
    turns.append(fmt_turn(p2, f"I saw a case series last month — {rand_int(8, 30)} patients with refractory {disease} responded to {random.choice(['CAR-T', 'allogeneic transplant', 'combination biologic', 'radioligand therapy'])}."))
    turns.append(fmt_turn(p1, f"What were the inclusion criteria?"))
    turns.append(fmt_turn(p2, f"ECOG {rand_int(0, 2)}, no prior {random.choice(['autoimmune disease', 'CNS involvement', 'allo-immunization'])}. This patient qualifies on {rand_int(2, 3)} of {rand_int(3, 5)} criteria."))
    turns.append(fmt_turn(p0, f"The family wants aggressive treatment. But the patient’s comorbidities — {random.choice(['Type 2 diabetes', 'hypertension', 'COPD', 'atrial fibrillation'])} — make high-intensity therapy risky."))
    turns.append(fmt_turn(p1, f"What does palliative care think?"))
    turns.append(fmt_turn(p0, f"They’re consulting tomorrow. My gut says offer the {random.choice(['CAR-T', 'transplant', 'biologic', 'radioligand'])} trial but set clear stop criteria."))

    if target_tokens >= 5000:
        turns.append(fmt_turn(p2, f"There’s a compassionate use program for {random.choice(['a novel JAK inhibitor', 'an anti-BCMA bispecific', 'a senolytic combination', 'a gene therapy vector'])}. Enrollment closes {deadline if 'deadline' in dir() else rand_date(2025, 2025)}."))
        turns.append(fmt_turn(p1, f"Is the mechanism aligned with this patient’s biomarker profile?"))
        turns.append(fmt_turn(p2, f"The patient is {random.choice(['PD-L1 positive', 'HER2 low', 'BRCA mutated', 'MSI-high'])}. The trial requires exactly that."))
        turns.append(fmt_turn(p0, f"That changes things. But compassionate use means no randomization. We lose the safety net."))
        turns.append(fmt_turn(p1, f"The patient is refractory to standard of care. At this stage, the risk-benefit shifts."))
        turns.append(fmt_turn(p2, f"I agree. We should present both options — trial versus best supportive care — and let the patient decide."))
        turns.append(fmt_turn(p0, f"I’ll schedule the family conference for {date2}. {p1}, can you pull the survival data from {rand_int(2, 5)} comparable cases?"))
        turns.append(fmt_turn(p1, f"I have {rand_int(4, 12)} cases in our registry. Median survival after {drug} failure is {rand_int(4, 14)} months."))

    if target_tokens >= 15000:
        turns.append(narr("The team reviews imaging and pathology slides for the next hour."))
        for i in range(rand_int(10, 18)):
            topic = random.choice([
                (p0, f"The PET-CT shows SUV max of {rand_int(8, 25)} in the {random.choice(['mediastinum', 'liver', 'spleen', 'bone marrow'])}."),
                (p1, f"That’s higher than I expected. Any necrosis?"),
                (p2, f"Patchy areas of central necrosis in {rand_int(2, 5)} of {rand_int(6, 12)} lesions."),
                (p0, f"Pathology from the {rand_date(2024, 2024)} biopsy showed {rand_int(60, 95)}% {random.choice(['lymphoid infiltrate', 'fibrosis', 'granulomatous reaction', 'necrosis'])}."),
                (p1, f"I’d like a second opinion from {rand_name()} at {hospital}. They specialize in {disease}."),
                (p2, f"I already sent the slides. Turnaround is {rand_int(5, 10)} business days."),
                (p0, f"The patient’s ECOG declined from {rand_int(0, 1)} to {rand_int(2, 3)} in the past {rand_int(2, 4)} weeks."),
                (p1, f"That’s a red flag. If it hits {rand_int(3, 4)} before the trial starts, they’re ineligible."),
                (p2, f"We could bridge with {random.choice(['dexamethasone', 'palliative radiation', 'transfusion support', 'IVIG'])}."),
                (p0, f"The family asked about quality of life. I told them we’d optimize for symptom control regardless of trial enrollment."),
                (p1, f"Good. Trust is fragile here."),
                (p2, f"Insurance pre-auth for {drug} was denied. I’m appealing. Reference number {rand_int(100000, 999999)}."),
            ])
            turns.append(fmt_turn(topic[0], topic[1]))
        turns.append(fmt_turn(p0, f"Decision: proceed with compassionate use application. {p1}, handle the informed consent. {p2}, manage the bridge therapy. Round again in {rand_int(12, 48)} hours."))

    conv = "\n\n".join(turns)
    return inject_naturalism(conv)


# --------------------------
# 5. Legal / Contract Negotiations
# --------------------------

def gen_legal(target_tokens: int) -> str:
    names = [rand_name() for _ in range(rand_int(3, 5))]
    company_a = rand_choice(COMPANIES)
    company_b = rand_choice(COMPANIES)
    while company_b == company_a:
        company_b = rand_choice(COMPANIES)
    term = rand_choice(LAW_TERMS)
    date1 = rand_date(2023, 2024)
    date2 = rand_date(2024, 2025)
    deal_value = rand_money(500, 10000)
    p0, p1, p2 = names[0], names[1], names[2]
    roles = random.choice([
        ("counsel", "counsel"),
        ("GC", "outside counsel"),
        ("partner", "associate"),
        ("CEO", "GC")
    ])

    turns = []
    turns.append(fmt_turn(p0, f"Thanks for joining, {p1}. We’re here to finalize the {company_a}–{company_b} acquisition term sheet. Deal value is {deal_value}."))
    turns.append(fmt_turn(p1, f"I’ve reviewed the draft from {date1}. There are {rand_int(4, 12)} open issues, but three are material."))
    turns.append(fmt_turn(p0, f"Walk me through them."))
    turns.append(fmt_turn(p1, f"First, the {term}. {company_a} wants uncapped liability for {random.choice(['data breaches', 'IP infringement', 'regulatory fines', 'tax indemnity'])}. That’s a non-starter."))
    turns.append(fmt_turn(p0, f"What’s their opening position?"))
    turns.append(fmt_turn(p1, f"{rand_money(5, 50)} cap, or {rand_int(10, 50)}% of purchase price, whichever is lower."))
    turns.append(fmt_turn(p0, f"Our board will accept a {rand_money(20, 100)} cap, but not uncapped. What’s your read on their flexibility?"))
    turns.append(fmt_turn(p1, f"Their {roles[1]} — {rand_name()} — signaled movement on a {rand_money(30, 150)} cap during the {random.choice(['breakfast', 'golf', 'dinner'])} last week. But their CFO is pushing back."))
    turns.append(fmt_turn(p2, f"I ran the risk model. Expected liability exposure is {rand_money(2, 20)}. Even a {rand_money(100, 200)} cap has {rand_int(80, 98)}% coverage probability."))
    turns.append(fmt_turn(p0, f"Good data. Use it in the counter. Second issue?"))
    turns.append(fmt_turn(p1, f"Earn-out structure. They want {rand_int(20, 40)}% of value tied to EBITDA targets over {rand_int(2, 4)} years."))
    turns.append(fmt_turn(p0, f"That’s standard. What’s the objection?"))
    turns.append(fmt_turn(p1, f"The targets are {rand_int(15, 40)}% above our internal projections. If we miss year one, the clawback is punitive."))

    if target_tokens >= 5000:
        turns.append(fmt_turn(p2, f"I modeled three scenarios: base case, bear case, stress case. In stress case, we lose {rand_int(30, 60)}% of the earn-out."))
        turns.append(fmt_turn(p0, f"Can we negotiate a sliding scale instead of cliff vesting?"))
        turns.append(fmt_turn(p1, f"That’s my recommendation. Pro-rata for every {rand_int(5, 15)}% of target achieved."))
        turns.append(fmt_turn(p0, f"Draft that language. Third issue?"))
        turns.append(fmt_turn(p1, f"Restrictive covenants. They want a {rand_int(18, 36)}-month non-compete for the founding team. Standard in this jurisdiction is {rand_int(6, 12)} months."))
        turns.append(fmt_turn(p2, f"And the non-solicit covers all employees, not just direct reports. That’s overbroad."))
        turns.append(fmt_turn(p0, f"Agreed. Counter with {rand_int(12, 18)} months non-compete, limited to {random.choice(['CTO', 'CEO', 'COO', 'founders'])} only. Non-solicit restricted to reports they personally hired."))
        turns.append(fmt_turn(p1, f"I’ll send the redline by {date2}. They want to sign by {rand_date(2025, 2025)} to hit their fiscal year-end."))

    if target_tokens >= 15000:
        turns.append(narr("The negotiation extends into the evening, with calls to both principals."))
        for i in range(rand_int(10, 18)):
            topic = random.choice([
                (p0, f"{company_b}’s counsel just called. They’re insisting on {random.choice(['a hell-or-high-water clause', 'a MAC carve-out for pandemics', 'no-shop provisions', 'breakup fee of 4%'])}."),
                (p1, f"A {rand_int(3, 5)}% breakup fee is market. Four percent is aggressive."),
                (p2, f"Our financing contingency expires on {rand_date(2025, 2025)}. We need certainty before then."),
                (p0, f"What about the reps and warranties insurance?"),
                (p1, f"Premium is {rand_money(100, 500)}. Retention is {rand_money(500, 1500)}. It covers {rand_int(18, 36)} months."),
                (p2, f"The insurer excluded {random.choice(['cyber liability', 'environmental', 'tax', 'employment practices'])}. We’d retain that risk."),
                (p0, f"Can we push some of that back to the seller through escrow?"),
                (p1, f"Escrow is currently {rand_int(8, 15)}% of purchase price. We could bump to {rand_int(12, 18)}% for {rand_int(12, 24)} months."),
                (p2, f"The seller will resist. Their liquidity is tight post-close."),
                (p0, f"Then we offer a seller note for the incremental escrow. {rand_int(4, 8)}% interest, {rand_int(12, 24)} month term."),
                (p1, f"That’s clever. It softens the blow."),
                (p2, f"Regulatory approval in {random.choice(['EU', 'UK', 'US', 'China'])} could take {rand_int(3, 9)} months. We need a long-stop date."),
                (p0, f"Propose {rand_date(2025, 2026)}. If no approval by then, either party walks with no breakup fee."),
                (p1, f"They’ll want the fee to survive. Let’s offer {rand_int(1, 2)}% in that scenario."),
            ])
            turns.append(fmt_turn(topic[0], topic[1]))
        turns.append(fmt_turn(p0, f"Alright. Send the revised terms tonight. I want a principled response by {date2}. If they’re close, we fly to {random.choice(['New York', 'London', 'Singapore', 'Dubai'])} for close."))

    conv = "\n\n".join(turns)
    return inject_naturalism(conv)


# --------------------------
# 6. Personal / Creative Writing Coaching
# --------------------------

def gen_creative(target_tokens: int) -> str:
    names = [rand_name() for _ in range(rand_int(2, 4))]
    coach = names[0]
    writer = names[1]
    genre = rand_choice(BOOK_GENRES)
    working_title = random.choice([
        "The Glass Orchard", "Last Train to Meridian", "Salt and Static",
        "Under the Amber Moon", "The Cartographer’s Lie", "Echo Chamber",
        "Bones of the Forest", "Midnight in the Archive", "The Dissolving City"
    ])
    word_count = rand_int(25000, 120000)
    draft_num = rand_int(2, 5)
    date1 = rand_date(2023, 2024)
    date2 = rand_date(2024, 2025)
    deadline = rand_date(2025, 2025)
    p0, p1 = coach, writer
    others = names[2:]

    turns = []
    turns.append(fmt_turn(p0, f"Okay, {p1}. I’ve read draft {draft_num} of \"{working_title}\". It’s {word_count} words. Let’s dig in."))
    turns.append(fmt_turn(p1, f"I’m bracing myself. What’s the verdict?"))
    turns.append(fmt_turn(p0, f"The prose is gorgeous. Your sentence-level craft is {random.choice(['exceptional', 'polished', 'sharp', 'lyrical'])}. But the structure is fighting the story."))
    turns.append(fmt_turn(p1, f"Where?"))
    turns.append(fmt_turn(p0, f"The midpoint happens at the {rand_int(55, 70)}% mark. That’s too late. By then, the reader’s patience is thinning."))
    turns.append(fmt_turn(p1, f"I was worried about that. The inciting incident is strong — the fire in chapter {rand_int(2, 5)} — but then I meander."))
    turns.append(fmt_turn(p0, f"Exactly. You have {rand_int(3, 6)} subplots that don’t converge. The {random.choice(['sibling rivalry', 'missing diary', 'estranged father', 'forged painting'])} thread just drops."))
    turns.append(fmt_turn(p1, f"I love that thread though. It reveals the protagonist’s {random.choice(['cowardice', 'obsession', 'guilt', 'ambition'])}."))
    turns.append(fmt_turn(p0, f"I know. But love isn’t enough. Either merge it with the A-plot or cut it. My instinct? Merge. Use the {random.choice(['diary', 'father', 'painting', 'rivalry'])} as the key to the climax."))
    turns.append(fmt_turn(p1, f"That could work. What about the antagonist? Beta readers say she’s {random.choice(['too vague', 'unsympathetic', 'one-dimensional', 'unmotivated'])}."))
    turns.append(fmt_turn(p0, f"She is. You tell us she’s cruel. But you never show us why she believes she’s righteous. Give her a {random.choice(['damaged childhood', 'lost child', 'betrayal', 'ideology'])}. Make her the hero of her own story."))
    turns.append(fmt_turn(p1, f"A POV chapter from her side?"))
    turns.append(fmt_turn(p0, f"One. Just one. In the second act. {rand_int(2000, 5000)} words. It’ll reframe everything."))

    if target_tokens >= 5000:
        turns.append(fmt_turn(p1, f"I’m worried about the genre. Is this {genre} or literary fiction with {genre} elements?"))
        turns.append(fmt_turn(p0, f"That’s your central tension. Right now it’s neither fish nor fowl. Agents will struggle to position it."))
        turns.append(fmt_turn(p1, f"So I need to commit. Lean into the {genre} tropes or strip them out entirely."))
        turns.append(fmt_turn(p0, f"Yes. And honestly? Your voice is literary. The {genre} framework is constraining you. I’d pull back the plot machinery and let the character study breathe."))
        turns.append(fmt_turn(p1, f"That’s scary. The market wants high-concept."))
        turns.append(fmt_turn(p0, f"The market wants voice. Look at {random.choice(['Ottessa Moshfegh', 'Sally Rooney', 'Colson Whitehead', 'Carmen Maria Machado', 'George Saunders'])}. They break every rule and sell."))
        turns.append(fmt_turn(p1, f"Okay. I'll write a new outline by {date1}. Cutting {rand_int(15000, 40000)} words."))
        turns.append(fmt_turn(p0, f"Don’t think of it as cutting. You’re excavating. The real book is under the rubble."))

    if target_tokens >= 15000:
        turns.append(narr("They spend the next hour workshopping a pivotal scene line by line."))
        for i in range(rand_int(10, 18)):
            topic = random.choice([
                (p0, f"This paragraph has {rand_int(5, 15)} adjectives in {rand_int(2, 4)} sentences. Pick one."),
                (p1, f"I was trying to create atmosphere."),
                (p0, f"Atmosphere comes from specificity, not density. Name the smell, not the feeling."),
                (p1, f"What if the protagonist never explains her motivation? Just acts?"),
                (p0, f"That can work if the actions are legible. If she burns the letter, the reader needs to know why without being told."),
                (p1, f"I want the reader to work for it."),
                (p0, f"There’s a difference between work and confusion. Right now it’s {random.choice(['70% confusion', 'too opaque', 'intellectually satisfying but emotionally distant'])}."),
                (p1, f"The love interest feels like a device."),
                (p0, f"Because they are. Either deepen them or remove them. A book can survive without romance."),
                (p1, f"My agent wants a synopsis by {deadline if 'deadline' in dir() else rand_date(2025, 2025)}. Should I write it from the new outline or the old draft?"),
                (p0, f"New outline. Agents smell desperation. If you pitch a book you’re still fixing, they’ll pass."),
                (p1, f"I got feedback from {rand_name()} at a workshop. They said the ending was \"earned but predictable.\""),
                (p0, f"Workshop feedback is a compass, not a map. Trust your own sense of the ending."),
            ])
            turns.append(fmt_turn(topic[0], topic[1]))
        turns.append(fmt_turn(p0, f"Final note: read {random.choice(['The Lover', 'Gilead', 'The Remains of the Day', 'Lincoln in the Bardo', 'A Visit from the Goon Squad'])}. Notice how silence operates. That’s what you’re missing."))
        turns.append(fmt_turn(p1, f"I’ll pick it up today. Next session?"))
        turns.append(fmt_turn(p0, f"{date2}. Bring the revised chapter {rand_int(1, 5)} and the antagonist POV."))

    conv = "\n\n".join(turns)
    return inject_naturalism(conv)


# ---------------------------------------------------------------------------
# Domain registry
# ---------------------------------------------------------------------------

DOMAIN_GENERATORS: Dict[str, Callable[[int], str]] = {
    "software_engineering": gen_software_eng,
    "product_management": gen_product_mgmt,
    "scientific_research": gen_science,
    "medical_case": gen_medical,
    "legal_negotiation": gen_legal,
    "creative_writing": gen_creative,
}

DOMAIN_NAMES = list(DOMAIN_GENERATORS.keys())


# ---------------------------------------------------------------------------
# Dataset generation
# ---------------------------------------------------------------------------

def expand_to_target(conv: str, target_tokens: int, domain: str) -> str:
    """
    If conversation is shorter than target, inject additional naturalistic
    back-and-forth segments until we cross the threshold.
    """
    while estimate_tokens(conv) < target_tokens:
        # Add a tangent / follow-up block
        extra_turns = []
        speakers = list(set(re.findall(r'^([A-Z][a-zA-Z\s]+?):', conv, re.MULTILINE)))
        if len(speakers) < 2:
            speakers = ["A", "B"]
        sp1, sp2 = speakers[0], speakers[1]

        tangent_starts = [
            f"Before we wrap, I wanted to circle back on something.",
            f"One more thing that just occurred to me.",
            f"I got an email about this earlier.",
            f"Actually, {sp2.lower()} reminded me —",
            f"Sorry to jump in, but —",
            f"Quick tangent:",
        ]
        tangent_ends = [
            f"Let’s park that for now.",
            f"We can revisit next time.",
            f"I’ll follow up offline.",
            f"Not urgent, just wanted to flag.",
            f"Anyway, back to the main thread.",
        ]

        extra_turns.append(fmt_turn(sp1, random.choice(tangent_starts)))
        # Add a few exchanges
        for _ in range(random.randint(2, 5)):
            if domain == "software_engineering":
                lines = [
                    (sp1, f"The monitoring setup is still flaky. {rand_int(3, 10)} false alerts last week."),
                    (sp2, f"We should tune the thresholds. Maybe {rand_int(2, 5)} sigma instead of {rand_int(1, 3)}?"),
                    (sp1, f"Also, {rand_name()} wants to migrate from {random.choice(['PagerDuty', 'OpsGenie', 'VictorOps'])} to {random.choice(['Datadog', 'Grafana OnCall', 'Splunk'])}."),
                    (sp2, f"Not now. Too many moving pieces."),
                    (sp1, f"Agreed. But the contract renews on {rand_date(2025, 2025)}."),
                ]
            elif domain == "product_management":
                lines = [
                    (sp1, f"Our Net Revenue Retention dropped to {rand_int(85, 105)}% last quarter."),
                    (sp2, f"Is that churn or downgrades?"),
                    (sp1, f"Mostly downgrades. {rand_int(2, 5)} enterprise accounts moved to a lower tier."),
                    (sp2, f"Pricing pressure from {rand_choice(COMPANIES)}?"),
                    (sp1, f"Exactly. They undercut us by {rand_int(20, 40)}% on annual contracts."),
                ]
            elif domain == "scientific_research":
                lines = [
                    (sp1, f"The preprint got {rand_int(2, 15)} comments on bioRxiv. One flagged a {random.choice(['statistical error', 'missing control', 'batch effect'])}."),
                    (sp2, f"Is it legitimate?"),
                    (sp1, f"I think so. The reviewer — {rand_name()} — has a solid reputation."),
                    (sp2, f"We should issue a corrigendum before peer review."),
                    (sp1, f"I’ll draft it. It doesn’t change the conclusion, just tightens the analysis."),
                ]
            elif domain == "medical_case":
                lines = [
                    (sp1, f"The patient’s family asked about hospice."),
                    (sp2, f"What did you tell them?"),
                    (sp1, f"That it’s premature, but we should have the conversation ready."),
                    (sp2, f"I agree. Early hospice discussions improve quality of life scores by {rand_int(10, 30)}%."),
                    (sp1, f"I’ll arrange a palliative care consult by {rand_date(2025, 2025)}."),
                ]
            elif domain == "legal_negotiation":
                lines = [
                    (sp1, f"The other side is pushing for {random.choice(['arbitration in London', 'Delaware jurisdiction', 'Swiss governing law'])}."),
                    (sp2, f"We prefer {random.choice(['New York', 'California', 'Singapore'])}. What’s the compromise?"),
                    (sp1, f"Maybe arbitration in {random.choice(['Hong Kong', 'Paris', 'Geneva'])} with {random.choice(['ICC', 'LCIA', 'SIAC'])} rules."),
                    (sp2, f"That’s palatable. But increase the tribunal to {rand_int(3, 5)} arbitrators."),
                    (sp1, f"That’ll slow things down. {rand_int(12, 24)} months instead of {rand_int(6, 12)}."),
                ]
            else:  # creative_writing
                lines = [
                    (sp1, f"I tried rewriting the opening in second person. It felt gimmicky."),
                    (sp2, f"Gimmicky or experimental?"),
                    (sp1, f"Gimmicky. Like a writing exercise, not a novel."),
                    (sp2, f"Then trust your instinct. First person present is working. Don’t fix what isn’t broken."),
                    (sp1, f"But {random.choice(['Publishers Weekly', 'a mentor', 'my MFA cohort'])} said first person is oversaturated."),
                ]
            for sp, txt in lines:
                extra_turns.append(fmt_turn(sp, txt))
        extra_turns.append(fmt_turn(sp2, random.choice(tangent_ends)))
        conv += "\n\n" + "\n\n".join(extra_turns)
    return conv


def generate_conversation(conv_id: str, domain: str, target_tokens: int, seed: int) -> Dict[str, Any]:
    """Generate a single synthetic conversation."""
    random.seed(seed)
    generator = DOMAIN_GENERATORS[domain]
    conv = generator(target_tokens)
    conv = expand_to_target(conv, target_tokens, domain)
    token_count = estimate_tokens(conv)
    return {
        "id": conv_id,
        "domain": domain,
        "token_count": token_count,
        "conversation": conv,
    }


def generate_dataset(output_path: str, n_short: int = 10, n_medium: int = 25, n_long: int = 15,
                     seed_offset: int = 42) -> Dict[str, Any]:
    """
    Generate the full benchmark dataset.

    Args:
        output_path: Where to write the JSONL file.
        n_short: Number of short conversations (target 2K–5K tokens).
        n_medium: Number of medium conversations (target 5K–15K tokens).
        n_long: Number of long conversations (target 15K–50K tokens).
        seed_offset: Base random seed for reproducibility.

    Returns:
        Statistics dictionary.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    tiers = [
        ("short", n_short, 2500, 4500),
        ("medium", n_medium, 6000, 14000),
        ("long", n_long, 18000, 48000),
    ]

    records: List[Dict[str, Any]] = []
    conv_idx = 0

    for tier_name, count, lo, hi in tiers:
        # Distribute domains roughly evenly, cycling through all 6
        domain_cycle = DOMAIN_NAMES * ((count // len(DOMAIN_NAMES)) + 1)
        random.seed(seed_offset + hash(tier_name) % 10000)
        random.shuffle(domain_cycle)
        selected_domains = domain_cycle[:count]

        for domain in selected_domains:
            target = random.randint(lo, hi)
            seed = seed_offset + conv_idx
            record = generate_conversation(
                conv_id=f"cap-bench-{tier_name}-{conv_idx:03d}",
                domain=domain,
                target_tokens=target,
                seed=seed,
            )
            records.append(record)
            conv_idx += 1

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Compute stats
    stats = {
        "total_conversations": len(records),
        "tiers": {},
        "domains": {},
        "total_tokens": sum(r["token_count"] for r in records),
    }
    for tier_name, count, lo, hi in tiers:
        tier_recs = [r for r in records if r["id"].startswith(f"cap-bench-{tier_name}")]
        tokens = [r["token_count"] for r in tier_recs]
        stats["tiers"][tier_name] = {
            "count": len(tier_recs),
            "min_tokens": min(tokens),
            "max_tokens": max(tokens),
            "mean_tokens": round(sum(tokens) / len(tokens), 1),
        }
    for d in DOMAIN_NAMES:
        d_recs = [r for r in records if r["domain"] == d]
        stats["domains"][d] = {
            "count": len(d_recs),
            "mean_tokens": round(sum(r["token_count"] for r in d_recs) / max(1, len(d_recs)), 1),
        }

    return stats


def load_dataset(path: str) -> List[Dict[str, Any]]:
    """Load the benchmark dataset from a JSONL file."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


if __name__ == "__main__":
    out = "benchmark/data/conversations.jsonl"
    stats = generate_dataset(out)
    print(json.dumps(stats, indent=2))
