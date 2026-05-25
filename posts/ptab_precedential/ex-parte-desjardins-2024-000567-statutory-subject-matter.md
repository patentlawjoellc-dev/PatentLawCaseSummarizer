---
title: "Ex parte Desjardins: PTAB Appeals Review Panel Treats Machine-Learning Model Improvement as Technological Improvement Under Step 2A, Prong 2, Vacating § 101 Rejection"
case_name: "Ex parte Desjardins"
designation: "Precedential"
decision_date: "2025-09-26"
designation_date: "2025-11-04"
proceeding_number: "2024-000567"
paper_number: ""
source_section: "Patent eligibility - 35 U.S.C. § 101"
source_subject_area: "Statutory subject matter"
tags:
  - "PTAB"
  - "Precedential"
  - "Patent eligibility - 35 U.S.C. § 101"
  - "Statutory subject matter"
  - "ARP"
  - "Step 2A Prong 2"
  - "Machine learning"
  - "Technological improvement"
  - "2019 Revised Guidance"
  - "Abstract idea"
  - "Practical application"
  - "Continual learning"
  - "Artificial intelligence"
pdf_url: "https://www.uspto.gov/sites/default/files/documents/202400567Ex_parte_Desjardins_arp_rehearing_decision.pdf"
uspto_page_url: "https://www.uspto.gov/patents/ptab/precedential-informative-decisions"
---

# Ex parte Desjardins: PTAB Appeals Review Panel Treats Machine-Learning Model Improvement as Technological Improvement Under Step 2A, Prong 2, Vacating § 101 Rejection

## Summary Card

**Precedential** · Ex parte Desjardins · 2024-000567
Decision date: 2025-09-26 · Designated: 2025-11-04
Subject area: Patent eligibility - 35 U.S.C. § 101 — Statutory subject matter
USPTO summary: [technological improvement to a machine learning model under step 2A, prong 2 of the 2019 revised guidance]
(ARP decision)

**Takeaway:** The PTAB Appeals Review Panel held that claims directed to training a machine learning model to sequentially learn new tasks while protecting performance on prior tasks constitute a technological improvement to the ML model itself — not merely an abstract mathematical concept — and therefore integrate the judicial exception into a practical application under Step 2A, Prong 2.

## Executive Takeaway

In this precedential Appeals Review Panel (ARP) decision authored by the Under Secretary of Commerce for Intellectual Property and Director of the USPTO, the panel vacated a Board panel's sua sponte new ground of rejection under 35 U.S.C. § 101. The claims at issue cover a computer-implemented method of training a machine learning model on sequential tasks — specifically, adjusting parameter values to optimize performance on a new task while protecting performance on a previously learned task, using a penalty term based on computed importance measures (approximations of posterior distributions). The ARP held that, even though the claims recite an abstract idea (a mathematical calculation), they are not directed to an abstract idea because the claimed method integrates that exception into a practical application under Step 2A, Prong 2.

The ARP's core holding is that improvements to how a machine learning model itself operates — including enabling continual learning across sequential tasks, reducing storage requirements by maintaining a single model instance, reducing system complexity, and protecting against 'catastrophic forgetting' — constitute technological improvements sufficient to satisfy Step 2A, Prong 2 under the 2019 Revised Guidance. The panel grounded its analysis in Enfish, emphasizing that software can make non-abstract improvements to computer technology and that the eligibility inquiry must turn on whether claims are directed to an improvement to computer functionality versus an abstract idea.

The ARP issued a pointed institutional warning: examiners and Board panels must not evaluate AI and machine learning claims at a high level of generality by categorically equating any machine learning with an unpatentable 'algorithm' and treating remaining claim elements as 'generic computer components' without adequate explanation. The panel also cautioned that sua sponte § 101 rejections must engage carefully with controlling precedent such as Enfish rather than substituting cursory analysis. The ARP further signaled that §§ 102, 103, and 112 — not § 101 — are the traditional and appropriate tools to limit patent protection to its proper scope.

This decision is binding precedent specifically on the question of whether a technological improvement to a machine learning model can satisfy Step 2A, Prong 2 of the 2019 Revised Guidance. It directly benefits AI patent applicants, prosecutors, and patent owners defending ML-related claims against § 101 rejections.

## Procedural Posture

Ex parte appeal from a final rejection in Application No. 16/319,040 (Technology Center 2100). A Board panel on March 4, 2025 affirmed a § 103 rejection of all pending claims (1–6, 8–20) and entered a new ground of rejection under § 101. The applicant filed a Request for Rehearing on May 5, 2025, which the Board panel denied on July 14, 2025. The Appeals Review Panel (ARP) — composed of the USPTO Director, the Acting Commissioner for Patents, and the Vice Chief Administrative Patent Judge — was then convened to review the Board's § 101 new ground of rejection. The ARP vacated the § 101 new ground of rejection and did not disturb the Board's other decisions (including the § 103 affirmance). The § 101 issue was thus resolved in the applicant's favor at Step 2A, Prong 2, without reaching Step 2B.

## Legal Issue

The central legal question is whether claims directed to a method of training a machine learning model on sequential tasks — using computed importance measures (approximations of posterior distributions over parameter values) to protect prior-task performance while optimizing for a new task — constitute a technological improvement to the machine learning model itself sufficient to integrate the recited abstract idea (mathematical calculation) into a practical application under Alice Step One / MPEP Step 2A, Prong 2 of the 2019 Revised Guidance. The relevant statute is 35 U.S.C. § 101; the governing framework is Alice Corp. v. CLS Bank (2014) as implemented through the USPTO's 2019 Revised Guidance (MPEP § 2106.04).

## Holding

The ARP held that independent claim 1 (and claims 18, 19, and all dependent claims) recites an abstract idea (a mathematical calculation — computing an approximation of a posterior distribution) but is not directed to an abstract idea, because the claim as a whole integrates the abstract idea into a practical application under Step 2A, Prong 2; specifically, the limitation requiring adjustment of parameter values to optimize performance on a second machine learning task while protecting performance on the first machine learning task constitutes an improvement to how the machine learning model itself operates, not merely an application of the mathematical calculation, and therefore the § 101 new ground of rejection was vacated.

## Reasoning

The ARP's Step 2A, Prong 1 analysis was undisputed: the Board had found that computing an approximation of a posterior distribution is a mathematical calculation (an abstract idea), and the applicant did not contest this finding. The ARP therefore accepted Prong 1 and proceeded to Prong 2.

At Prong 2, the Board panel below had found no additional element or combination of elements that integrated the judicial exception into a practical application. The applicant argued on rehearing that the claims reflect an improvement in the functioning of a computer or an improvement to other technology or technical field under MPEP §§ 2106.04(d)(1) and 2106.05(a), citing paragraph 21 of the Specification for the propositions that: (1) the claimed method addresses 'catastrophic forgetting' in continual learning systems; (2) it reduces storage requirements by maintaining a single model instance rather than multiple instances; and (3) it reduces system complexity.

The ARP agreed with the applicant. Applying Enfish's framework, the ARP emphasized that software can make non-abstract improvements to computer technology and that the eligibility determination must turn on whether claims are directed to an improvement to computer functionality versus an abstract idea. The ARP identified the following specific claim limitation as reflecting the technological improvement: 'adjust the first values of the plurality of parameters to optimize performance of the machine learning model on the second machine learning task while protecting performance of the machine learning model on the first machine learning task.' The ARP was persuaded this constitutes an improvement to how the machine learning model itself operates — distinct from the mathematical calculation.

The ARP noted that a Specification assertion of improvement alone is insufficient; the claim itself must reflect the disclosed improvement (citing MPEP § 2106.05(a) and Intellectual Ventures I v. Symantec). Here, the ARP found the claim did reflect the improvement.

Critically, the ARP issued an institutional rebuke of the Board panel's reasoning: the panel had 'essentially equated any machine learning with an unpatentable algorithm and the remaining additional elements as generic computer components, without adequate explanation,' and had evaluated claims at too high a level of generality. The ARP found this approach inconsistent with Enfish and warned that such categorical exclusion of AI innovations from patent protection jeopardizes U.S. leadership in AI. The ARP also criticized the panel's sua sponte action for ignoring well-settled precedent.

The ARP further signaled a policy preference: §§ 102, 103, and 112 are the traditional and appropriate tools to limit patent protection to its proper scope, and examination should focus there rather than on § 101 for AI innovations that are adequately described and nonobvious. The § 103 rejection was left undisturbed, so the claims remain rejected — but on obviousness grounds, not eligibility.

## Key Quotes

> "We are persuaded that constitutes an improvement to how the machine learning model itself operates, and not, for example, the identified mathematical calculation."
> *(p. p. 10)*

> "Categorically excluding AI innovations from patent protection in the United States jeopardizes America's leadership in this critical emerging technology. Yet, under the panel's reasoning, many AI innovations are potentially unpatentable—even if they are adequately described and nonobvious—because the panel essentially equated any machine learning with an unpatentable 'algorithm' and the remaining additional elements as 'generic computer components,' without adequate explanation."
> *(p. p. 9)*

> "Examiners and panels should not evaluate claims at such a high level of generality."
> *(p. p. 9)*

> "This case demonstrates that §§ 102, 103 and 112 are the traditional and appropriate tools to limit patent protection to its proper scope. These statutory provisions should be the focus of examination."
> *(p. p. 10)*

> "Software can make non-abstract improvements to computer technology, just as hardware improvements can, [and] the eligibility determination should turn on whether the claims are directed to an improvement to computer functionality versus being directed to an abstract idea."
> *(p. p. 9)*

## Why This Matters for Patent Litigators

**Petitioners:**
- IPR petitioners challenging AI/ML patents on § 101 grounds face a higher bar: this decision signals that the PTAB leadership views § 101 as an inappropriate primary vehicle for limiting AI patent scope.
- Petitioners relying on categorical 'algorithm = abstract idea' arguments for ML claims will find that reasoning expressly rejected at the highest PTAB level.
- Petitioners should focus invalidity challenges on §§ 102, 103, and 112 rather than § 101 for ML-related claims, consistent with the ARP's explicit policy signal.
- Arguments that ML training methods are merely mathematical operations applied to generic computer components are now directly contradicted by binding precedent.

**Patent Owners:**
- Patent owners defending ML-related claims against § 101 challenges — in IPR, PGR, ex parte prosecution, or district court — can cite this decision as binding PTAB precedent that improvements to ML model operation (continual learning, catastrophic forgetting prevention, storage reduction) satisfy Step 2A, Prong 2.
- Patent owners should identify and emphasize in their claim language and specifications the specific operational improvements to the ML model itself, not just the mathematical steps.
- The decision supports the argument that a single claim limitation reflecting a technological improvement to ML model operation is sufficient to integrate an abstract idea into a practical application.
- Patent owners can use the ARP's rebuke of high-generality analysis to push back against examiner or Board rejections that treat all ML as abstract without claim-specific analysis.

**District Court Litigators:**
- Defense counsel asserting § 101 invalidity for ML patents must now contend with this precedential ARP decision, which will be cited by patentees to defeat Alice Step One arguments.
- The decision reinforces that district courts should conduct claim-specific, limitation-level analysis rather than categorical treatment of ML as abstract — consistent with Enfish.
- Litigators should expect patentees to argue that any ML claim limitation reflecting an improvement to model operation (e.g., efficiency, multi-task performance, reduced storage) satisfies Step 2A, Prong 2.
- The ARP's policy statement about AI leadership may influence judicial receptivity to § 101 challenges against ML patents in the current political and technological climate.
- Defense counsel should focus invalidity strategy on §§ 102 and 103 for ML patents rather than § 101, consistent with the ARP's explicit guidance.

**Prosecutors:**
- Prosecutors drafting AI/ML patent applications should include explicit claim language directed to improvements in how the ML model itself operates — not just the mathematical steps — to satisfy Step 2A, Prong 2 under this precedent.
- Specification paragraphs should clearly articulate technological improvements such as: prevention of catastrophic forgetting, reduced storage requirements, reduced system complexity, and improved multi-task performance — and claims should affirmatively recite limitations that reflect those improvements.
- When responding to § 101 rejections, prosecutors should invoke this decision to argue that ML model operational improvements integrate the abstract idea into a practical application, and should identify the specific claim limitation(s) that reflect the improvement.
- Prosecutors should avoid relying solely on Specification assertions of improvement; the ARP confirmed that the claim itself must reflect the disclosed improvement.
- The ARP's warning against high-generality analysis gives prosecutors a basis to demand claim-specific, limitation-level § 101 analysis from examiners and to traverse rejections that merely label ML as 'algorithm' without engaging with specific claim elements.
- Prosecutors should draft claims that recite the functional outcome of the ML improvement (e.g., 'while protecting performance on the first task') rather than only the mathematical mechanism, to anchor the Prong 2 analysis.
- The ARP's signal that §§ 102, 103, and 112 are the preferred examination tools for AI claims suggests prosecutors may have more success arguing § 101 eligibility while accepting that prior art and enablement will be the real battlegrounds.

## Practice Tips

- Draft ML patent claims to include at least one limitation that affirmatively recites the operational improvement to the ML model itself (e.g., 'while protecting performance on the first machine learning task'), not just the mathematical steps — this is the type of limitation the ARP identified as sufficient for Prong 2.
- In prosecution, when facing a § 101 rejection on ML claims, cite Ex parte Desjardins as binding PTAB precedent and argue that the specific claim limitation(s) reflecting ML model operational improvements (continual learning, catastrophic forgetting prevention, storage reduction, system complexity reduction) integrate the abstract idea into a practical application.
- Ensure the Specification explicitly ties the claimed improvements to the ML model's operation — reduced storage, reduced system complexity, multi-task performance preservation — and then confirm those improvements are reflected in the claim language itself, not just the Specification.
- Invoke the ARP's institutional warning against high-generality analysis to require examiners and Board panels to conduct limitation-level § 101 analysis rather than categorical treatment of ML as abstract.
- For district court § 101 challenges to ML patents, shift invalidity strategy to §§ 102 and 103 as the primary vehicles, consistent with the ARP's explicit policy signal that these are the 'traditional and appropriate tools' for limiting AI patent scope.
- When responding to sua sponte § 101 rejections by Board panels, cite the ARP's criticism of the panel below for ignoring Enfish and acting without adequate explanation — this decision creates a procedural and substantive basis to challenge cursory sua sponte § 101 rejections.
- For patent owners in IPR/PGR proceedings, use this decision to argue that § 101 is not the appropriate vehicle for challenging ML patents and that the Board should focus on prior art grounds.
- When claiming ML improvements, consider framing the improvement in terms of both the ML model's internal operation (parameter adjustment, task protection) and the system-level benefits (storage reduction, complexity reduction) to maximize the Prong 2 argument under multiple MPEP § 2106.05 factors.

## Related Decisions

- Enfish, LLC v. Microsoft Corp., 822 F.3d 1327 (Fed. Cir. 2016) — cited as the leading Federal Circuit case on eligibility of technological improvements to software/computer functionality
- Alice Corp. Pty. Ltd. v. CLS Bank Int'l, 573 U.S. 208 (2014) — foundational two-step § 101 framework
- McRO, Inc. v. Bandai Namco Games Am. Inc., 837 F.3d 1299 (Fed. Cir. 2016) — cited for improvement to technology or technical field
- AI Visualize, Inc. v. Nuance Commc'ns, Inc., 97 F.4th 1371 (Fed. Cir. 2024) — cited for 'directed to' inquiry under Alice Step One
- Affinity Labs of Tex. v. DirecTV, LLC, 838 F.3d 1253 (Fed. Cir. 2016) — cited for claims merely linking judicial exception to particular technological environment
- Elec. Power Grp., LLC v. Alstom S.A., 830 F.3d 1350 (Fed. Cir. 2016) — cited for claims merely linking judicial exception to field of use
- Intellectual Ventures I LLC v. Symantec Corp., 838 F.3d 1307 (Fed. Cir. 2016) — cited for proposition that Specification assertion of improvement alone is insufficient; claim must reflect the improvement
- Mayo Collaborative Servs. v. Prometheus Labs., Inc., 566 U.S. 66 (2012) — cited for 'significantly more' / inventive concept standard

## Caveats

- The PDF text was fully extractable and the ARP decision is complete; no extraction failures noted.
- This decision is designated Precedential ONLY for the specific issue of technological improvement to a machine learning model under Step 2A, Prong 2 of the 2019 Revised Guidance — it is NOT designated precedential for the § 103 rejection, which was affirmed and left undisturbed.
- The § 103 rejection of all claims (1–6, 8–20) was affirmed by the Board and not disturbed by the ARP; the claims therefore remain rejected on obviousness grounds despite the § 101 victory.
- The ARP's policy statements about AI leadership and the preference for §§ 102/103/112 over § 101 are institutional guidance and commentary, not formal holdings — their precedential weight as legal rules is limited to the § 101 Step 2A, Prong 2 holding.
- The decision does not reach Step 2B (significantly more / inventive concept) because the ARP resolved the case at Step 2A, Prong 2; the precedential holding is confined to Prong 2 analysis.
- The ARP panel consisted of the USPTO Director, Acting Commissioner for Patents, and Vice Chief Administrative Patent Judge — this is the highest-level PTAB panel; however, the decision's precedential scope is defined by the USPTO's designation, not the panel composition.
- No paper number is listed in the USPTO metadata; the decision is identified by proceeding number 2024-000567 and application number 16/319,040.
- The PDF does not contain page numbers in the traditional sense; page references in key_quotes are based on the sequential page count of the extracted PDF text as provided.