# Session 1 — cloud certification study kickoff

**User:** I want to get cloud certified this year. I'm leaning AWS since most job postings around me ask for it. Can you help me build a study plan?

**Assistant:** Good choice to start with a target. AWS Solutions Architect Associate (SAA-C03) is the standard entry point for the roles you're describing. Before we plan, what's your background — any hands-on cloud experience?

**User:** I've deployed a couple of side projects on EC2 and used S3 for storage, but nothing formal. I can study about 6 hours a week.

**Assistant:** That's a workable base. At 6 hours a week, a realistic timeline is 10 weeks. Here's the plan:

- Weeks 1–2: IAM, EC2, VPC fundamentals — the exam is heavy on networking
- Weeks 3–4: S3, EBS, EFS storage classes and lifecycle policies
- Weeks 5–6: RDS, DynamoDB, ElastiCache
- Weeks 7–8: high availability — ELB, Auto Scaling, Route 53, CloudFront
- Weeks 9–10: practice exams, review weak areas

**User:** Sounds good. Decision: I'm committing to the AWS Solutions Architect Associate exam, targeting 10 weeks from now, studying 6 hours per week.

**Assistant:** Committed. One important habit: do the labs hands-on in a free-tier account, don't just watch videos. The exam punishes passive studying. Want me to set up week 1's IAM exercises?

**User:** Yes please. Also let's use Tutorials Dojo practice exams when I get to week 9, I've heard they're the closest to the real thing.

**Assistant:** Agreed — Tutorials Dojo (Jon Bonso's sets) are the community standard for SAA-C03. For week 1, start with these IAM exercises:

1. Create an IAM user with programmatic access and attach a least-privilege policy
2. Write a custom policy that allows S3 read on one bucket only, test the deny
3. Set up an IAM role for EC2 and access S3 from an instance without keys

**User:** Perfect, I'll start tonight with the IAM user exercise. One more thing — should I worry about exam cost?

**Assistant:** The SAA-C03 exam is $150 USD. Budget for one practice-exam set (~$15) on top. Some employers reimburse certification costs after you pass — worth asking before you pay out of pocket.

**User:** Good tip, I'll check the reimbursement policy at work tomorrow.
