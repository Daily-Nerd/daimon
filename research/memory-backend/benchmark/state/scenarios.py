"""Hand-authored multi-turn scenarios with KNOWN state evolution.

Each scenario is a sequence of turns where several state variables are set and
then overridden. Because we author them, the *current* value of every variable
after the last turn is known exactly — so grading is deterministic, not an LLM
guess. Probes flagged `is_override=True` are the discriminating ones: a memory
that keeps stale values will assert the old value and fail them.

Design rules for authored scenarios (keep them honest):
- gold and stale tokens must be lexically distinct so substring grading is sound
  (e.g. "Rust" vs "Go"/"Python", not "Go" vs "Golang").
- include distractor turns (noise) so memory must actually consolidate, not just
  echo the last line.
- every override variable changes value at least once AFTER it was first stated.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class Probe:
    id: str
    question: str
    gold: str                       # canonical CURRENT value
    gold_aliases: List[str] = field(default_factory=list)
    stale: List[str] = field(default_factory=list)   # now-wrong values; must not be asserted as current
    is_override: bool = False       # True if this variable changed during the conversation

    def gold_terms(self) -> List[str]:
        return [self.gold] + list(self.gold_aliases)


@dataclass
class Scenario:
    id: str
    domain: str
    turns: List[str]
    probes: List[Probe]


SCENARIOS: List[Scenario] = [
    Scenario(
        id="infra-migration",
        domain="software_infra",
        turns=[
            "User: Kicking off the platform rework. For new services the team will write everything in Go.",
            "Assistant: Got it — Go for new services. What's the infra budget picture?",
            "User: CFO approved a 30% infra cost cut over the next two quarters. Backend team is 12 engineers.",
            "Assistant: Understood. Any orchestration decision yet?",
            "User: Leaning toward Nomad to replace Kubernetes. It's simpler for our scale.",
            "Assistant: Noted, Nomad as the direction. Anything on observability?",
            "User: We'll standardize on OpenTelemetry. Also, quick correction — the perf-critical ingestion path will be Rust, not Go. Go stays for everything else.",
            "Assistant: So Rust for the ingestion path, Go elsewhere. Understood.",
            "User: Another update from finance: the cut got revised down to 20%, not 30%. A bit more breathing room.",
            "Assistant: 20% cut now. Got it.",
            "User: And after a spike, we're abandoning Nomad — staying on Kubernetes after all. The migration risk wasn't worth it.",
            "Assistant: Final call: stay on Kubernetes. Team size still 12?",
            "User: Yes, still 12 engineers.",
        ],
        probes=[
            Probe("infra-lang", "What language will be used for the performance-critical ingestion path?",
                  gold="Rust", gold_aliases=["rust"], stale=["Go", "Golang"], is_override=True),
            Probe("infra-orch", "What is the current orchestration decision?",
                  gold="Kubernetes", gold_aliases=["k8s", "kubernetes"], stale=["Nomad"], is_override=True),
            Probe("infra-budget", "What is the current approved infrastructure cost cut percentage?",
                  gold="20", gold_aliases=["20%", "twenty"], stale=["30", "30%", "thirty"], is_override=True),
            Probe("infra-team", "How many engineers are on the backend team?",
                  gold="12", gold_aliases=["twelve"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="product-roadmap",
        domain="product_management",
        turns=[
            "User: Planning the next release. Target launch is Q3.",
            "Assistant: Q3 launch. What's the headline feature?",
            "User: The flagship is the new search experience. Pricing will be per-seat.",
            "Assistant: Search flagship, per-seat pricing, Q3. Anything on the team?",
            "User: We added two designers. Standups stay at 9am.",
            "Assistant: Noted. Any changes to scope?",
            "User: Big shift: leadership wants billing revamp as the flagship instead of search. Search drops to a fast-follow.",
            "Assistant: So billing is now the flagship, search is fast-follow. Understood.",
            "User: Also pricing is moving to usage-based, not per-seat. Finance wants consumption alignment.",
            "Assistant: Usage-based pricing now. Got it.",
            "User: And the date slipped — launch is Q4 now, not Q3. The billing work needs the extra runway.",
            "Assistant: Q4 launch. Confirmed.",
        ],
        probes=[
            Probe("prod-flagship", "What is the current flagship feature for the release?",
                  gold="billing", gold_aliases=["billing revamp"], stale=["search"], is_override=True),
            Probe("prod-pricing", "What is the current pricing model?",
                  gold="usage-based", gold_aliases=["usage based", "consumption"], stale=["per-seat", "per seat"], is_override=True),
            Probe("prod-date", "What is the current target launch quarter?",
                  gold="Q4", gold_aliases=["fourth quarter"], stale=["Q3", "third quarter"], is_override=True),
        ],
    ),
    Scenario(
        id="care-plan",
        domain="clinical_fiction",
        turns=[
            "User: Reviewing the care plan for the patient. Starting them on medication Alfarin.",
            "Assistant: Alfarin started. What dose?",
            "User: 50mg once daily. Follow-up scheduled in 6 weeks.",
            "Assistant: 50mg daily, 6-week follow-up. Noted.",
            "User: Patient tolerated the first week. Diet guidance: low-sodium.",
            "Assistant: Low-sodium diet added. Anything else?",
            "User: Change of plan — Alfarin caused a rash, switching the patient to medication Betoxil instead.",
            "Assistant: Discontinue Alfarin, start Betoxil. Understood.",
            "User: Betoxil dose is 75mg once daily. Titrate up only if symptoms persist.",
            "Assistant: Betoxil 75mg daily. Got it.",
            "User: Follow-up window stays at 6 weeks from today.",
            "Assistant: Confirmed, 6-week follow-up.",
        ],
        probes=[
            Probe("care-med", "What medication is the patient currently prescribed?",
                  gold="Betoxil", gold_aliases=["betoxil"], stale=["Alfarin"], is_override=True),
            Probe("care-dose", "What is the current daily dose?",
                  gold="75mg", gold_aliases=["75 mg", "75"], stale=["50mg", "50 mg", "50"], is_override=True),
            Probe("care-followup", "When is the follow-up scheduled?",
                  gold="6 weeks", gold_aliases=["six weeks"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="assistant-prefs",
        domain="personal_assistant",
        turns=[
            "User: Set up my assistant preferences. I'm vegetarian.",
            "Assistant: Vegetarian noted. How about meeting times?",
            "User: I prefer mornings for meetings. Contact me by email for anything non-urgent.",
            "Assistant: Mornings, email for non-urgent. Anything else?",
            "User: My home airport is SFO. I like aisle seats.",
            "Assistant: SFO, aisle seats. Got it.",
            "User: Update: I've gone fully vegan now, not just vegetarian. Please apply that to all restaurant suggestions.",
            "Assistant: Vegan for all suggestions. Understood.",
            "User: Also flip my meeting preference to afternoons — mornings stopped working with my schedule.",
            "Assistant: Afternoons for meetings now. Noted.",
            "User: And switch my non-urgent contact channel from email to Slack.",
            "Assistant: Slack for non-urgent contact. Confirmed.",
        ],
        probes=[
            Probe("pa-diet", "What is the user's current dietary preference?",
                  gold="vegan", gold_aliases=[], stale=["vegetarian"], is_override=True),
            Probe("pa-meeting", "What time of day does the user now prefer meetings?",
                  gold="afternoon", gold_aliases=["afternoons"], stale=["morning", "mornings"], is_override=True),
            Probe("pa-channel", "What is the user's current channel for non-urgent contact?",
                  gold="Slack", gold_aliases=["slack"], stale=["email"], is_override=True),
            Probe("pa-airport", "What is the user's home airport?",
                  gold="SFO", gold_aliases=[], stale=[], is_override=False),
        ],
    ),
    # ------------------------------------------------------------------
    # Scenarios 5-12 widen the probe pool with override SHAPES the first
    # four don't cover: double-override chains, reverts, entity swaps,
    # late overrides, scoped (sibling-preserving) changes, numeric drift,
    # status flips, and coupled rename+ownership transfers.
    # ------------------------------------------------------------------
    Scenario(
        id="vendor-selection",
        domain="procurement",
        turns=[
            "User: Starting the payments vendor evaluation. Initial pick is Stripe.",
            "Assistant: Stripe as the starting candidate. Contract terms?",
            "User: We're targeting a 12-month contract. Settlement currency will be EUR.",
            "Assistant: 12 months, EUR settlement. Noted.",
            "User: Legal flagged Stripe's data residency terms. We're moving the pick to Adyen.",
            "Assistant: Adyen is the candidate now. Anything else?",
            "User: Procurement wants more leverage, so the contract target is now 24 months instead of 12.",
            "Assistant: 24-month target. Got it.",
            "User: Final twist — Adyen's quote came in 40% over budget. We're signing with Braintree.",
            "Assistant: Braintree it is. Settlement still EUR?",
            "User: Yes, EUR stays.",
        ],
        probes=[
            Probe("vend-vendor", "Which payments vendor are we currently going with?",
                  gold="Braintree", gold_aliases=["braintree"],
                  stale=["Stripe", "Adyen"], is_override=True),
            Probe("vend-term", "What is the current contract length target in months?",
                  gold="24", gold_aliases=["24-month", "twenty-four"],
                  stale=["12", "12-month", "twelve"], is_override=True),
            Probe("vend-currency", "What is the settlement currency?",
                  gold="EUR", gold_aliases=["euro", "euros"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="office-relocation",
        domain="operations",
        turns=[
            "User: Planning the European HQ. We're setting up in Lisbon.",
            "Assistant: Lisbon for the HQ. How big is the office?",
            "User: Space for 40 people. Lease budget is 60k per month.",
            "Assistant: 40 seats, 60k monthly. Noted.",
            "User: Change of direction — the exec team prefers Madrid for the talent pool. HQ moves to Madrid.",
            "Assistant: Madrid instead of Lisbon. Understood.",
            "User: We toured spaces in Madrid this week. Also, IT picked Cisco for office networking gear.",
            "Assistant: Cisco for networking. How did the tours go?",
            "User: Badly — Madrid rents blew past budget. We're reverting to Lisbon, the original plan.",
            "Assistant: Back to Lisbon, confirmed. Seats still 40?",
            "User: Still 40 people, yes.",
        ],
        probes=[
            Probe("ofc-city", "In which city is the European HQ currently planned?",
                  gold="Lisbon", gold_aliases=["lisbon"],
                  stale=["Madrid"], is_override=True),
            Probe("ofc-seats", "How many people is the office sized for?",
                  gold="40", gold_aliases=["forty"], stale=[], is_override=False),
            Probe("ofc-network", "Which vendor was picked for office networking gear?",
                  gold="Cisco", gold_aliases=["cisco"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="team-leads",
        domain="org_management",
        turns=[
            "User: Setting up the app teams. Marisol leads the mobile team, Tobias leads the web team.",
            "Assistant: Marisol on mobile, Tobias on web. Team sizes?",
            "User: Mobile has 5 engineers, web has 7. Sprint length is two weeks for both.",
            "Assistant: 5 and 7, two-week sprints. Noted.",
            "User: The design review moved to Thursdays, by the way.",
            "Assistant: Thursday design reviews. Anything else?",
            "User: Org shuffle: Marisol and Tobias are swapping teams. Tobias takes mobile, Marisol takes web.",
            "Assistant: Swap confirmed — Tobias now leads mobile, Marisol now leads web.",
            "User: Right. Sprint cadence and team sizes stay as they were.",
            "Assistant: Understood, only the leads changed.",
        ],
        probes=[
            Probe("lead-mobile", "Who currently leads the mobile team?",
                  gold="Tobias", gold_aliases=["tobias"],
                  stale=["Marisol"], is_override=True),
            Probe("lead-web", "Who currently leads the web team?",
                  gold="Marisol", gold_aliases=["marisol"],
                  stale=["Tobias"], is_override=True),
            Probe("lead-mobile-size", "How many engineers are on the mobile team?",
                  gold="5", gold_aliases=["five"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="research-grant",
        domain="academia",
        turns=[
            "User: Preparing the lab's grant application. We're applying to the Hartwell Foundation.",
            "Assistant: Hartwell Foundation. What's the ask?",
            "User: 1.2 million over three years. The PI is Dr. Okafor.",
            "Assistant: 1.2M over three years, Dr. Okafor as PI. Noted.",
            "User: The methods section needs a rewrite — the reviewer pool there is heavy on statisticians.",
            "Assistant: Will flag the methods section. Anything else?",
            "User: We hired a grants administrator, starts Monday. Also the budget office wants quarterly reporting.",
            "Assistant: New grants admin, quarterly reporting. Got it.",
            "User: The biostatistics core agreed to co-author. Their letter of support arrives Friday.",
            "Assistant: Letter from biostat core incoming. Status on the application?",
            "User: Big news — Hartwell paused the program. We're submitting to the Whitaker Trust instead.",
            "Assistant: Whitaker Trust now. Same ask?",
            "User: Same ask, 1.2 million.",
        ],
        probes=[
            Probe("grant-funder", "Which funder are we currently submitting the application to?",
                  gold="Whitaker", gold_aliases=["Whitaker Trust"],
                  stale=["Hartwell"], is_override=True),
            Probe("grant-amount", "What is the grant ask amount?",
                  gold="1.2", gold_aliases=["1.2 million", "1.2M"], stale=[], is_override=False),
            Probe("grant-pi", "Who is the PI on the application?",
                  gold="Okafor", gold_aliases=["Dr. Okafor"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="deploy-regions",
        domain="software_infra",
        turns=[
            "User: Locking the deployment map. EU traffic serves from Frankfurt, US traffic from Virginia.",
            "Assistant: Frankfurt for EU, Virginia for US. Failover plans?",
            "User: Failover is cross-region within the same continent. CDN is Fastly.",
            "Assistant: Continental failover, Fastly CDN. Noted.",
            "User: Compliance update: EU workloads must move to Stockholm for the new data residency rules. US stays exactly where it is.",
            "Assistant: EU moves to Stockholm, US unchanged in Virginia. Understood.",
            "User: Correct. And we renewed the Fastly contract for two more years.",
            "Assistant: Fastly renewed. Anything else?",
            "User: No, the region map is final now.",
        ],
        probes=[
            Probe("region-eu", "Which region currently serves EU traffic?",
                  gold="Stockholm", gold_aliases=["stockholm"],
                  stale=["Frankfurt"], is_override=True),
            # Sibling-preservation probe: US never changed; a memory that
            # over-generalizes the EU move will wrongly assert Stockholm here.
            Probe("region-us", "Which region currently serves US traffic?",
                  gold="Virginia", gold_aliases=["virginia"],
                  stale=["Stockholm", "Frankfurt"], is_override=False),
            Probe("region-cdn", "Which CDN provider are we using?",
                  gold="Fastly", gold_aliases=["fastly"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="sales-quota",
        domain="sales_ops",
        turns=[
            "User: Setting Q2 targets. Each rep carries a quota of 100 units.",
            "Assistant: 100 units per rep. Pricing?",
            "User: List price is 80 dollars per unit. Commission stays at 8 percent.",
            "Assistant: 80 per unit, 8% commission. Noted.",
            "User: Leadership raised the bar — quota jumps to 250 units per rep.",
            "Assistant: 250 units now. That's aggressive.",
            "User: The kickoff event moved to the Denver office, by the way.",
            "Assistant: Denver kickoff. Anything else?",
            "User: After the field pushed back, quota settled at 175 units per rep. Final.",
            "Assistant: 175 units, final. Price unchanged?",
            "User: Price unchanged at 80 dollars.",
        ],
        probes=[
            Probe("quota-units", "What is the current per-rep quota in units?",
                  gold="175", gold_aliases=[],
                  stale=["100", "250"], is_override=True),
            Probe("quota-price", "What is the list price per unit in dollars?",
                  gold="80", gold_aliases=["eighty"], stale=[], is_override=False),
            Probe("quota-commission", "What is the commission percentage?",
                  gold="8", gold_aliases=["8%", "eight"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="trial-protocol",
        domain="clinical_fiction",
        turns=[
            "User: The Phase II trial protocol for compound Xelorin is approved as of today.",
            "Assistant: Xelorin Phase II approved. Enrollment target?",
            "User: 120 participants across 4 sites. Primary endpoint is symptom score at week 8.",
            "Assistant: 120 participants, 4 sites, week-8 endpoint. Noted.",
            "User: Site onboarding starts next week. The CRO handling logistics is Meridian Research.",
            "Assistant: Meridian Research as CRO. Anything else?",
            "User: Serious development — the safety board flagged liver enzyme elevations. The trial is suspended, effective immediately.",
            "Assistant: Trial suspended. Does enrollment stop too?",
            "User: Everything stops. Enrollment target stays on paper at 120 for if and when we resume.",
            "Assistant: Understood — suspended, target unchanged on paper.",
        ],
        probes=[
            Probe("trial-status", "What is the current status of the Xelorin trial?",
                  gold="suspended", gold_aliases=["suspension", "halted", "on hold"],
                  stale=["approved"], is_override=True),
            Probe("trial-enroll", "What is the enrollment target?",
                  gold="120", gold_aliases=[], stale=[], is_override=False),
            Probe("trial-cro", "Which CRO is handling trial logistics?",
                  gold="Meridian", gold_aliases=["Meridian Research"], stale=[], is_override=False),
        ],
    ),
    Scenario(
        id="project-rename",
        domain="software_infra",
        turns=[
            "User: New internal project, codename Falcon. Priya owns it. Repo lives on GitLab.",
            "Assistant: Falcon, owned by Priya, on GitLab. Scope?",
            "User: It's the customer-data export pipeline. Deadline is end of September.",
            "Assistant: Export pipeline, September deadline. Noted.",
            "User: Legal says Falcon collides with a trademark. The codename is now Heron everywhere.",
            "Assistant: Renamed to Heron. Docs updated?",
            "User: Updating them now. Also Priya is leaving the company — Dmitri takes over ownership of Heron.",
            "Assistant: Dmitri owns Heron now. Deadline unchanged?",
            "User: Deadline holds, end of September. Repo stays on GitLab.",
            "Assistant: September on GitLab, confirmed.",
        ],
        probes=[
            Probe("proj-name", "What is the current codename of the project?",
                  gold="Heron", gold_aliases=["heron"],
                  stale=["Falcon"], is_override=True),
            Probe("proj-owner", "Who currently owns the project?",
                  gold="Dmitri", gold_aliases=["dmitri"],
                  stale=["Priya"], is_override=True),
            Probe("proj-host", "Where is the project repo hosted?",
                  gold="GitLab", gold_aliases=["gitlab"], stale=[], is_override=False),
        ],
    ),
]


def all_scenarios() -> List[Scenario]:
    return SCENARIOS
