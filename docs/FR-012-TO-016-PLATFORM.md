# FR-012–016 — Institution platform

**Priority:** P2 | **Status:** planned for the full product

These five requirements extend VIORA-AI beyond the hackathon brief. They belong to the V4 institutional architecture. Build only the minimum identity, membership and authorization needed for P0 before attempting the full system.

## FR-012 — Institution structure and authorization

Model organizations, terms, classes, enrollments, subjects, subject offerings, teacher assignments and user memberships. One user may hold several roles, but every request must be evaluated against the **active institution**, membership status, the requested action and the resource's relationship to that user.

Core records: `Organization`, `AcademicTerm`, `ClassGroup`, `Enrollment`, `SubjectOffering`, `TeacherAssignment`, `Membership`, `RoleGrant`, `ResourceGrant`, `AuditEvent`.

A platform admin can manage the service without automatically gaining access to private student data or course materials. Creatorship, ownership and sharing scope are not the same field. Suspended memberships must stop new protected actions even when the user has an old token.

**Tests:** teachers see assigned classes only; students see enrolled content; directors get approved aggregates; cross-institution requests fail; suspended users cannot publish or start jobs. Authorization decisions are logged without disclosing private content.

**Open:** DEC-003's detailed permission model, inheritance, ownership transfer, revocation latency, cross-institution sharing, support access and whether RLS is used.

## FR-013 — Learning evidence, mastery and recommendations

Use assessment attempts and learning interactions as an **append-only evidence ledger**. Each event records the learner, tenant, question/concept version, rubric, score, time and origin. Maintain a versioned concept/prerequisite map.

Derive `MasteryEstimate` records from evidence using a transparent initial rule: weighted recent performance, coverage of distinct questions and an uncertainty indicator. This is easier to explain and test than immediately introducing an opaque predictive model. If there is little evidence, report *insufficient evidence* rather than a confident score.

A recommendation should say *what to practice and why*, with links to the failed concept, prerequisite and relevant lesson. Teachers can adjust or dismiss it; changing a rubric recomputes a new estimate without altering historical attempts.

**Tests:** a teacher can trace an estimate back to question versions and attempts; duplicate events have no effect; a changed rubric leaves the raw evidence intact; learners cannot inspect another student's evidence.

**Open:** weighting formula, uncertainty thresholds, curriculum concept graph, student privacy and evaluation for disparate error rates.

## FR-014 — Teacher intervention and remediation

Turn a weak-concept signal into something an educator can review. Suggested workflow:

`Signal → Teacher review → Assign practice/lesson → Student response → Outcome review`.

Each step records actor, time, scope, linked evidence and decision. Recommendations are **not** automatic disciplinary or high-stakes student decisions. The system must show when the evidence is weak, and teachers should be able to dismiss a false alarm.

**Tests:** only the assigned educator can approve an intervention; learner receives exactly the scoped assignment; states/audit history remain visible; reported outcomes distinguish observed improvement from a claim of causation.

**Open:** notification cadence, educator workflow, escalation policies and acceptable use of automated recommendations.

## FR-015 — Governed sharing

Teachers may share a lesson or a `KnowledgeDrop` with an assigned class, another authorized educator or an institution-defined audience. Grant scope, resource owner, publisher, visibility and asset rights must remain separate.

A shared lesson keeps its lineage to the original source, media and question versions. On new reads and publication attempts, check current grants and revocation state. Do not expose answer keys or private teacher notes through the student-facing manifest. Cross-institution sharing stays disabled until DEC-003 is resolved.

**Tests:** two users with different grants see different resources; revoked links stop new authorized requests; a copied or remixed lesson keeps rights/source lineage; no public media URL bypasses the access policy.

**Open:** ownership transfer, policy inheritance, licensed assets, offline copies and revocation delay.

## FR-016 — Analytics

Build student, class and institution views from the same authoritative attempt and lesson events used by the learning system. Use derived read models or materialized summaries for performance, but keep a reproducible path back to the underlying records.

Examples: assigned lessons completed, question accuracy by concept, lessons needing review, teaching content usage and intervention follow-through. Class and institution views must honor role scope and small-group privacy thresholds. Show data freshness and denominator definitions so staff can interpret metrics correctly.

**Tests:** totals reconcile against a known fixture set; replaying an event does not double count; teachers cannot inspect unrelated classes; small-group suppression works; updated data is eventually reflected and marked with freshness time.

**Open:** reporting definitions, minimum cohort size, retention policy, refresh schedule, dashboard technology and whether analytics exports are allowed.

## Build order

The P0 platform needs minimal authentication, tenant ownership and authorized teacher/student views. Complete the broader FR-012 institution model before opening P2 sharing. Then build FR-013's evidence model, FR-016's reconciled aggregate views and FR-014's teacher decisions. FR-015 sharing can expand only after the revocation and ownership model is specified.
