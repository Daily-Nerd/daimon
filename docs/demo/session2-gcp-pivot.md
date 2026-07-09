# Session 2 — pivot: new job runs on GCP

**User:** Big update since last time. I got a job offer and accepted it — the whole company runs on Google Cloud, not AWS. I start in three weeks.

**Assistant:** Congratulations! That changes the study plan materially. Are you thinking of pivoting your certification target to GCP?

**User:** Yes. Decision: I'm dropping the AWS Solutions Architect prep entirely. New target is the Google Cloud Associate Cloud Engineer exam, since that's what my new team recommended for onboarding.

**Assistant:** Sensible pivot — the Associate Cloud Engineer (ACE) is the right first GCP cert, and your AWS groundwork transfers better than you'd think. The concepts map almost one-to-one:

- IAM → Cloud IAM (roles are more granular in GCP)
- EC2 → Compute Engine
- S3 → Cloud Storage
- VPC → VPC (global by default in GCP — this difference shows up on the exam)
- RDS → Cloud SQL

**User:** Good to know the effort wasn't wasted. Can we rework the plan to fit the three weeks before I start?

**Assistant:** Yes — tighter but the goal shifts: get functional before day one, certify after you're settled. Three-week plan:

- Week 1: Cloud IAM, Compute Engine, gcloud CLI basics — live in Cloud Shell
- Week 2: Cloud Storage, VPC networking, Cloud SQL
- Week 3: GKE fundamentals and deployment patterns — most GCP shops run Kubernetes

Then schedule the ACE exam for 4–6 weeks after your start date, once you've seen real workloads.

**User:** That works. Cancel the Tutorials Dojo purchase idea too — I'll use Google's official skills boost program instead since my new employer gives free credits for it.

**Assistant:** Noted — Google Cloud Skills Boost with employer credits replaces Tutorials Dojo. Start with the "Cloud Engineer Learning Path" there; it maps directly to the ACE exam guide.

**User:** Great. I'll begin the Cloud IAM module tonight. Does the ACE exam cost the same as the AWS one?

**Assistant:** Slightly less — the Associate Cloud Engineer exam is $125 USD versus $150 for AWS SAA. And since your new employer sponsors Skills Boost, ask them about exam vouchers too; many GCP partner companies get them.

**User:** Will do, I'll ask HR about vouchers during onboarding week.
