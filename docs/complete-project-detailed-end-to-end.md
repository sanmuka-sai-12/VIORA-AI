VIORA AI — Complete End-to-End Product Architecture and Business Blueprint
Complete B2B2C Product | All User Roles | All Dashboards | Role Relationships | AI Systems | LLM Evaluation Integration | Business Model | Technical Architecture | End-to-End Workflows

This is the consolidated product baseline for VIORA AI.

One important decision is included in this version:

LLM Evaluation will be developed as a separate, independent project and integrated privately into VIORA AI's backend and internal Super Admin dashboard.

Students, parents, teachers and institutions will use VIORA's normal learning features. They will not see the technical LLM evaluation system.

The product is still one connected platform. The separate evaluation project is an internal quality-engineering system.

1. VIORA AI — Product Definition
VIORA AI is an institution-first, AI-powered learning and academic intelligence platform.

It connects:
1. Educational institutions.
2. Management and administrators.
3. Teachers.
4. Students.
5. Parents or guardians.
6. VIORA's internal platform team.
The main objective is to transform an institution's academic resources into structured, trusted, interactive learning experiences.

VIORA provides:
- Academic content management.
- Institution-specific knowledge management.
- AI-powered doubt solving.
- Syllabus-based learning.
- AI-generated explanations and study materials.
- Previous-year question banks.
- Practice questions.
- Quizzes and examinations.
- Student performance analytics.
- Personalised learning recommendations.
- Mistake tracking and revision.
- Optional AI visual lessons and animations.
- Parent visibility into their child's academic progress.
- Institutional academic intelligence.
- Internal AI quality evaluation.
VIORA is not simply an LMS, AI chatbot, PDF summariser or video generator.

It is a connected academic ecosystem.
1.1 Main product philosophy
The platform has three main objectives:

For institutions: Manage academic resources, teachers, students, performance and learning operations.

For teachers: Reduce repetitive academic work and provide tools for teaching, assessments and performance analysis.

For students: Help them understand concepts, clear doubts, practise, prepare for examinations and improve.

For parents: Provide appropriate visibility into their child's academic engagement and progress.

For VIORA: Maintain platform quality, manage business operations, control AI costs and continuously improve AI reliability.

2. B2B2C Business Model
VIORA follows an institution-first B2B2C business model.

The institution purchases the platform and provides access to its teachers and students. Parent access can also be enabled by the institution.

The commercial relationship is:

VIORA AI → Educational Institution → Teachers and Students → Parents/Guardians

The institution is the primary paying customer.

Teachers, students and authorised parents are platform users.
2.1 How the business works
1. A college, university or coaching institute contacts VIORA.
2. VIORA demonstrates the product.
3. The institution selects a subscription plan.
4. VIORA creates its private institutional workspace.
5. The institution appoints an authorised administrator.
6. The administrator configures departments, courses, classes and subjects.
7. Teachers and students are added.
8. Parents can be invited and linked to their children, if enabled.
9. Teachers upload academic resources.
10. VIORA processes and structures the resources.
11. Teachers review and publish the material.
12. Students access their learning environment.
13. Parents receive authorised progress information.
14. The institution monitors academic and adoption reports.
15. VIORA monitors platform usage, costs and quality.
16. The institution renews or upgrades its subscription.
2.2 Revenue model
The recommended commercial structure is:
- Institution platform subscription.
- Student seat pricing.
- Optional teacher or premium module pricing.
- Included AI usage credits.
- Additional AI usage packs.
- Optional advanced visual-learning features.
- Additional integration and onboarding charges where applicable.
Direct B2C subscriptions can be introduced later. They should not distract from the first institutional product.

3. Complete User Roles and Their Relationships
VIORA needs a proper role and permission hierarchy.

There are two different types of users:
1. Internal VIORA users.
2. External institutional users.
3.1 Internal VIORA users
Role	Main responsibility
Platform Super Admin	Controls the complete VIORA business and platform
Platform Operations Admin	Manages support, onboarding and operations
AI/Technical Admin	Manages AI services, models, usage and infrastructure
Academic Content Admin	Manages standard syllabus packs and academic quality
LLM Evaluation Admin	Manages evaluation datasets, tests, reports and release quality

These are VIORA employees or authorised operators.
3.2 External institutional users
Role	Main responsibility
Institution Owner	Owns the institution's VIORA contract and commercial relationship
Institution Admin	Manages the institution's workspace
HOD/Department Admin	Manages an assigned department
Class Teacher/Class Coordinator	Manages assigned classes and coordinates academic activities
Subject Teacher	Manages assigned subjects and learning material
Student	Uses learning, practice and assessment features
Parent/Guardian	Views authorised information about their linked child

A person may have more than one authorised role. For example, a teacher may also be a class coordinator.

Permissions must be checked by both role and assignment.

4. Complete User Relationship Hierarchy
The following is the logical relationship structure of VIORA.

Institution-level academic hierarchy:

Institution
→ Campus or Branch
→ Department
→ Programme or Course
→ Academic Year
→ Semester
→ Section or Batch
→ Subject
→ Chapter
→ Topic
→ Subtopic
→ Concept

User assignment hierarchy:

Institution
→ Institution Admin
→ HOD/Department Admin
→ Teacher
→ Class
→ Student
→ Parent/Guardian

The actual access rules are not simply based on this hierarchy. They are based on membership, permissions and explicit assignments.

For example:
- An HOD can access only assigned departments.
- A teacher can access only assigned classes and subjects.
- A class teacher can coordinate assigned students and classes.
- A subject teacher can manage the content and assessments for assigned subjects.
- A student can access only their enrolled classes and published resources.
- A parent can access only their linked child's authorised information.
- An Institution Admin can manage the institution's workspace.
- A VIORA Super Admin can manage the platform but does not routinely access private student learning conversations.
4.1 Parent-child relationship
A parent account must be explicitly linked to a student account.

The relationship is:

Parent Account
→ Verified Guardian Relationship
→ Student Account
→ Institution Membership
→ Assigned Class
→ Academic Records

A parent may have multiple children.

A student may have more than one authorised guardian.

The institution must define the guardian-verification and consent process.

Parent access should be limited to:
- Academic progress.
- Attendance, if integrated and authorised.
- Test performance.
- Assignments.
- Teacher announcements.
- Learning activity summaries.
- Areas where the child may need support.
- Parent-teacher communication, if enabled.
Parents should not automatically receive access to:
- Private student conversations.
- Every AI tutor prompt.
- Personal notes.
- Sensitive student information.
- Teacher-only reports.
- Other students' data.
For minors, privacy, consent and applicable legal requirements need particular attention.

5. Dashboard 1 — VIORA Platform Super Admin
This is the main dashboard belonging to the VIORA company.

It is not the dashboard of any college or university.

It controls the entire VIORA platform.
5.1 Super Admin Overview
The main dashboard should show:
- Total institutions.
- Active institutions.
- Suspended institutions.
- Total registered users.
- Active teachers.
- Active students.
- Active parent accounts.
- Monthly active users.
- Total subscriptions.
- Monthly recurring revenue.
- Annual recurring revenue.
- Total AI requests.
- AI consumption.
- AI costs.
- Platform infrastructure costs.
- Failed AI requests.
- Failed background jobs.
- Active support tickets.
- System health.
5.2 Institution Management
The Super Admin can:
- Create institutions.
- Edit institution details.
- Activate institutions.
- Suspend institutions.
- Configure institution plans.
- Set student seat limits.
- Set teacher seat limits if applicable.
- Enable and disable modules.
- Set AI usage limits.
- View onboarding progress.
- View institutional usage.
- View renewal dates.
- View contract details.
- Manage institutional support.
5.3 Subscription and Revenue Management
Features:
- Subscription plans.
- Pricing configurations.
- Institution contracts.
- Trial accounts.
- Paid pilot accounts.
- Active subscriptions.
- Expired subscriptions.
- Renewal management.
- Upgrade and downgrade requests.
- Seat management.
- AI credits.
- Additional usage.
- Invoices.
- Payment status.
- Revenue analytics.
5.4 User Management
The Super Admin can manage platform-level accounts.
- Internal employees.
- Platform administrators.
- Operations employees.
- Academic reviewers.
- Technical administrators.
- LLM evaluation administrators.
Institutional user management should normally remain with the institution.
5.5 AI Model Management
This is the technical control centre.

Features:
- LLM provider configuration.
- Model configuration.
- Embedding model configuration.
- Reranking configuration.
- OCR configuration.
- Speech services.
- Visual generation services.
- Model fallback rules.
- API health.
- Model latency.
- Token consumption.
- Request failure rate.
- Provider costs.
- Institution-level quotas.
- Model version tracking.
- Prompt version tracking.
5.6 AI Usage and Cost Management
For every AI operation, track:
- Institution ID.
- User ID or pseudonymous user reference.
- Feature name.
- Model used.
- Input tokens.
- Output tokens.
- Processing duration.
- Estimated provider cost.
- Success or failure.
- Timestamp.
- Usage credits deducted.
The system should provide cost reports by institution, department and feature.
5.7 Academic Pack Management
The Super Admin can manage reusable academic packs.

Examples:
- University engineering syllabus packs.
- Computer Science subjects.
- AI and Machine Learning subjects.
- JEE preparation.
- NEET preparation.
- Other competitive examination packs.
Each pack should be versioned.

Institutions can adopt packs and customise their local syllabus mappings.
5.8 Support and Operations
- Institution onboarding requests.
- Technical tickets.
- Teacher support.
- Student support.
- Billing issues.
- AI service failures.
- Document processing failures.
- Account problems.
- Service incidents.
5.9 Security and Audit
- Platform access history.
- Admin activity.
- Permission changes.
- Institution suspension.
- Data export history.
- Data deletion history.
- Suspicious login activity.
- Security incidents.
- Administrative access logs.

6. Dashboard 2 — VIORA Internal AI and LLM Evaluation Dashboard
This is a critical addition.

It is separate from the student, teacher and institution dashboards.

It is also different from the ordinary Super Admin overview.

Its job is to answer:

Is VIORA's AI actually working correctly, consistently and safely?

The LLM Evaluation project will be developed independently and connected to VIORA through a controlled backend integration.
6.1 Important architectural decision
Develop two independent software projects:

Project A: VIORA AI — Main Product

Project B: VIORA LLM Evaluation Platform — Independent AI Quality System

Project B can be reused with other AI applications in the future.

Project A consumes evaluation results and quality status through a defined integration.

Do not mix the evaluation engine's entire implementation into the main product's business logic.
6.2 What should be evaluated?
VIORA contains several AI features. Each requires different evaluation criteria.
A. RAG Evaluation
Check:
- Did retrieval find the correct academic information?
- Was the retrieved context relevant?
- Were important source passages missed?
- Did the answer use the retrieved context?
- Were citations correct?
- Did the AI invent unsupported information?
- Was the answer relevant to the student's question?
B. AI Tutor Evaluation
Check:
- Answer correctness.
- Explanation quality.
- Conceptual clarity.
- Syllabus alignment.
- Reasoning consistency.
- Citation accuracy.
- Uncertainty handling.
- Student-level appropriateness.
C. Question Generation Evaluation
Check:
- Is the generated question correct?
- Is the answer key correct?
- Is the difficulty appropriate?
- Is it mapped to the correct topic?
- Is it within the syllabus?
- Is it duplicated?
- Is it ambiguous?
- Are the distractor options reasonable?
D. Content Generation Evaluation
Check:
- Are notes accurate?
- Are summaries faithful to sources?
- Are formulas correct?
- Are worked examples mathematically correct?
- Are diagrams consistent with the explanation?
- Is important information missing?
E. Safety and Security Evaluation
Check:
- Prompt injection.
- Malicious instructions inside uploaded files.
- Data leakage.
- Cross-institution information leakage.
- Inappropriate responses.
- Unauthorised content access.
- Sensitive data exposure.
F. Performance Evaluation
Check:
- Response time.
- Failure rate.
- Token consumption.
- Cost per operation.
- Model fallback frequency.
- Reliability under load.
6.3 LLM Evaluation Dashboard Pages
1. Evaluation Overview.
2. Evaluation Dataset Management.
3. Test Case Management.
4. RAG Evaluation.
5. Tutor Evaluation.
6. Question Generation Evaluation.
7. Content Generation Evaluation.
8. Citation Evaluation.
9. Safety Evaluation.
10. Model Comparison.
11. Prompt Version Comparison.
12. Regression Testing.
13. Production Quality Monitoring.
14. Failure Analysis.
15. Evaluation Reports.
16. Release Quality Gates.
6.4 Example evaluation report
Suppose VIORA's tutor answers a question about gradient descent.

The evaluation system can check:
Evaluation	Example result
Answer correctness	94%
Citation validity	98%
Context relevance	91%
Faithfulness to retrieved sources	95%
Average response time	2.4 seconds
Estimated request cost	Recorded per request

These are illustrative numbers, not actual performance claims.
6.5 How the independent evaluation project integrates
The integration relationship is:

VIORA Backend
→ AI Feature
→ Model Gateway
→ AI Response
→ Evaluation Event
→ LLM Evaluation Platform
→ Evaluation Result
→ VIORA Internal Quality Dashboard

There should be two kinds of evaluation.

Offline evaluation:
- Test a fixed dataset.
- Compare different models.
- Compare prompt versions.
- Test retrieval improvements.
- Test before releases.
- Run regression suites.
Online evaluation:
- Sample production interactions.
- Evaluate response quality.
- Monitor citation errors.
- Track failure patterns.
- Monitor model changes.
- Detect quality degradation.
Do not run every expensive evaluation synchronously before returning an answer to a student. That would increase latency and AI costs.

Use asynchronous evaluation, sampling and selected critical checks. Safety and access-control checks must still happen in the live request path.
6.6 How the Super Admin sees it
The main Super Admin dashboard should contain an AI Quality section.

It displays:
- Overall AI quality status.
- Latest evaluation run.
- Failed evaluation categories.
- Current model performance.
- Prompt version performance.
- RAG quality.
- Tutor quality.
- Question generation quality.
- Safety alerts.
- Production evaluation trends.
When the administrator opens the AI Quality section, the connected evaluation platform provides the detailed reports.

Students, parents, teachers and institutions do not need to see the evaluation platform.

They simply receive the improved VIORA AI product.

7. Dashboard 3 — Institution Owner Dashboard
This is for the person or organisation that purchases VIORA.

It can be the college management, director, principal or authorised owner, depending on the institution's structure.

The owner is primarily concerned with:
- Contract.
- Subscription.
- Institutional adoption.
- Overall academic outcomes.
- Department performance.
- Business value.
- Renewal.
The Institution Owner may have all Institution Admin permissions or a restricted commercial and overview role.

Features:
1. Institution overview.
2. Subscription and contract details.
3. Seat utilisation.
4. Department performance.
5. Teacher adoption.
6. Student adoption.
7. Academic performance summaries.
8. Platform usage.
9. AI consumption.
10. Cost and billing.
11. Renewal and upgrade requests.
12. Institution-level reports.
The Owner should not automatically have unrestricted access to every private student conversation.

8. Dashboard 4 — Institution Admin
The Institution Admin manages the institution's actual VIORA workspace.
8.1 Overview
- Total students.
- Total teachers.
- Total departments.
- Total programmes.
- Total classes.
- Active users.
- Uploaded resources.
- Published content.
- Assessments conducted.
- Institution-level performance.
- AI usage.
- Subscription status.
8.2 Organisation Management
Create and manage:
- Campuses.
- Departments.
- Programmes.
- Courses.
- Academic years.
- Semesters.
- Sections.
- Batches.
- Subjects.
- Class groups.
8.3 HOD and Department Management
- Add HODs.
- Assign departments.
- Configure permissions.
- Assign teachers.
- Monitor departmental academic activities.
- View department analytics.
- Review departmental resources.
- Monitor academic progress.
8.4 Teacher Management
- Add teachers.
- Bulk import teachers.
- Assign subjects.
- Assign sections.
- Assign classes.
- Assign class coordinator roles.
- Activate and deactivate accounts.
- View teaching activity.
- View uploaded resources.
- View assessment activity.
8.5 Student Management
- Add students.
- Import student data.
- Assign student IDs.
- Assign programmes.
- Assign semesters.
- Assign sections.
- Enrol students in subjects.
- Move students between classes.
- Activate or deactivate accounts.
- Link authorised guardians.
8.6 Parent Management
- Invite parents or guardians.
- Verify guardian relationships.
- Link parents to students.
- Manage consent where required.
- Manage parent account access.
- Remove outdated guardian links.
- View parent engagement summaries.
8.7 Academic Management
- Configure syllabus.
- Adopt academic packs.
- Manage subjects.
- Manage chapters.
- Map topics.
- Assign academic resources.
- Configure academic calendar.
- Track syllabus coverage.
8.8 Resource Management
- View all institutional resources.
- Filter by subject.
- Filter by teacher.
- View upload status.
- View processing failures.
- Review approval status.
- Manage content versions.
- Archive resources.
- Withdraw resources.
- Monitor publication.
8.9 Assessments
- View tests created by teachers.
- Monitor scheduled tests.
- View test participation.
- View subject-level performance.
- View class-level performance.
- View concept-level performance.
8.10 Institution Analytics
- Student adoption.
- Teacher adoption.
- Weekly active users.
- Monthly active users.
- Content coverage.
- Test participation.
- Subject performance.
- Chapter performance.
- Concept weaknesses.
- Students needing support.
- AI usage by department.
- Observed teacher time saved.
8.11 Billing and Usage
- Subscription plan.
- Active seats.
- Available seats.
- AI credits.
- Consumption.
- Usage limits.
- Invoices.
- Renewal dates.
- Upgrade requests.

9. Dashboard 5 — HOD / Department Admin
This dashboard is for department-level academic management.

An HOD should not need to operate the entire institution dashboard.

They manage their assigned department.

Example:

CSE Department HOD
→ CSE Faculty
→ CSE Classes
→ CSE Subjects
→ CSE Students

Features:
- Department overview.
- Assigned teachers.
- Assigned programmes.
- Academic year management within permissions.
- Subject allocation.
- Class allocation.
- Resource coverage.
- Teacher activity.
- Syllabus progress.
- Test performance.
- Class comparisons.
- Concept-level weaknesses.
- Department reports.
- Students requiring academic attention.
The HOD can review performance and coordinate interventions without automatically editing every student's private learning information.

10. Dashboard 6 — Class Teacher / Class Coordinator
This is different from an ordinary subject teacher.

A class coordinator manages the overall academic coordination of an assigned class.

Example:

CSE 2nd Year, Section A.

Features:
- Class overview.
- Student list.
- Subject teacher list.
- Academic announcements.
- Class schedule information, if integrated.
- Assignment overview.
- Test calendar.
- Class performance.
- Subject-wise performance.
- Student participation.
- Students needing attention.
- Parent communication, if authorised.
- Class-level reports.
A class coordinator may teach a subject too, but their additional coordination permissions should be explicitly assigned.

11. Dashboard 7 — Subject Teacher
The Subject Teacher manages their assigned subjects and classes.

This is the main academic creation workspace.
11.1 Teacher Overview
- Assigned subjects.
- Assigned classes.
- Total students.
- Recent uploads.
- Pending reviews.
- Published content.
- Upcoming tests.
- Student doubts.
- Class performance.
- Weak concepts.
11.2 My Classes
For each class:
- Student list.
- Subject details.
- Published content.
- Chapter coverage.
- Assignments.
- Tests.
- Results.
- Student analytics.
11.3 My Subjects
For each subject:
- Syllabus.
- Chapters.
- Topics.
- Subtopics.
- Concepts.
- Uploaded resources.
- Generated content.
- Question bank.
- PYQs.
- Learning reports.
11.4 Resource Upload
The teacher can upload:
- PDFs.
- Notes.
- Documents.
- Worksheets.
- Question papers.
- Supported images.
- Other supported academic files.
VIORA processes and structures these resources.

The teacher reviews them before publication.
11.5 AI Content Studio
The teacher can create:
- Detailed explanations.
- Short notes.
- Summaries.
- Formula sheets.
- Worked examples.
- Diagrams.
- Mind maps.
- Flashcards.
- Lesson outlines.
- Practice questions.
- Optional animated lessons.
11.6 Question Bank
- Institution questions.
- Teacher-created questions.
- Licensed questions.
- Verified PYQs.
- AI-generated draft questions.
Filter by:
- Subject.
- Chapter.
- Topic.
- Concept.
- Difficulty.
- Exam.
- Year.
- Marks.
11.7 Quiz and Test Management
The teacher can:
1. Select a class.
2. Select a subject.
3. Select a chapter.
4. Select questions.
5. Generate additional draft questions if needed.
6. Review answers.
7. Configure duration.
8. Configure marks.
9. Configure attempts.
10. Select students.
11. Publish or schedule.
The teacher can conduct a test whenever needed.

The platform must not force teachers to create tests.
11.8 Student Doubt Management
- View escalated doubts.
- Answer questions.
- View recurring misunderstandings.
- Publish clarifications.
- Assign remedial material.
- Recommend additional practice.
11.9 Teacher Analytics
- Class accuracy.
- Topic accuracy.
- Chapter performance.
- Student progress.
- Frequently incorrect questions.
- Common mistakes.
- Students needing attention.
- Results over time.

12. Dashboard 8 — Student
This is the central learning environment.

The student should not experience VIORA as an administrative platform. They should experience it as a personalised academic workspace.
12.1 Student Home
- My subjects.
- Continue learning.
- Recent topics.
- Upcoming tests.
- Assignments.
- Teacher announcements.
- Recommended revision.
- Learning progress.
- Notifications.
12.2 My Subjects
The student sees only their assigned subjects.

Each subject contains:

Subject
→ Chapter
→ Topic
→ Subtopic
→ Concept
12.3 Learn Workspace
Each topic may provide:
- Detailed explanation.
- Short notes.
- Study notes.
- Formulas.
- Worked examples.
- Diagrams.
- Mind maps.
- Teacher resources.
- AI-generated approved resources.
- Optional animation.
- Related questions.
12.4 AI Tutor
Students can ask doubts.

Example:
- Explain this concept.
- Explain the formula.
- Give me a simple example.
- Explain this question step by step.
- Why is my answer incorrect?
- Give me a similar problem.
The AI Tutor has two modes.

Learn Mode:
- Detailed explanation.
- Simple explanation.
- Examples.
- Analogies.
- Follow-up questions.
- Source references.
Practice Mode:
- Ask the student to attempt.
- Provide hints.
- Give partial guidance.
- Explain the solution.
- Check understanding.
12.5 Practice
- Topic-wise questions.
- Concept-wise questions.
- Difficulty selection.
- Random practice.
- Similar questions.
- Explanations.
- Attempt history.
12.6 PYQ Centre
For supported examination packs:
- Select examination.
- Select year.
- Select subject.
- Select chapter.
- Select topic.
- Solve questions.
- View explanations.
- View source information.
- Practise related questions.
12.7 Tests and Assignments
- Available quizzes.
- Scheduled tests.
- Assignments.
- Test instructions.
- Time limits.
- Attempt history.
- Scores.
- Teacher feedback.
- Correct answers when permitted.
12.8 My Progress
- Subject progress.
- Chapter progress.
- Topic progress.
- Practice accuracy.
- Assessment results.
- Strong concepts.
- Weak concepts.
- Improvement over time.
12.9 Mistake Book
Automatically organise mistakes into categories:
- Conceptual misunderstanding.
- Wrong formula.
- Calculation mistake.
- Unit error.
- Sign error.
- Application mistake.
- Prerequisite gap.
- Time pressure.
12.10 Revision Centre
- Saved notes.
- Bookmarked questions.
- Flashcards.
- Formula revision.
- Weak-concept revision.
- Suggested practice.
- Previous mistakes.
- Revision history.

13. Dashboard 9 — Parent / Guardian
The Parent Dashboard is an additional stakeholder experience.

It should not become another teacher or admin dashboard.

The parent's main purpose is to understand the child's learning progress and communicate with the institution when appropriate.
13.1 Parent Home
- Linked children.
- Child's current programme.
- Academic progress summary.
- Upcoming tests.
- Recent results.
- Pending assignments.
- Teacher announcements.
- Areas needing attention.
13.2 Child Academic Overview
For each linked child:
- Institution.
- Department or programme.
- Academic year.
- Class.
- Enrolled subjects.
- Subject-wise progress.
- Chapter-wise progress.
- Test performance.
- Assignment completion.
- Learning engagement.
13.3 Performance Reports
- Recent test scores.
- Subject-wise performance.
- Improvement trends.
- Strong areas.
- Areas requiring additional practice.
- Teacher feedback.
- Recommended support.
13.4 Attendance
Attendance should only appear if the institution integrates or manages attendance through VIORA.

It is not automatically available just because VIORA provides learning tools.
13.5 Parent-Teacher Communication
If enabled:
- Request a meeting.
- Send a message.
- View teacher responses.
- Receive academic announcements.
- Receive meeting notifications.
13.6 Notifications
- Test schedules.
- Important academic announcements.
- Report availability.
- Assignment reminders.
- Parent-teacher meeting notifications.
- Significant academic concerns, subject to institutional policy.
13.7 Parent Privacy Rules
Parents should not automatically read the child's AI Tutor conversations.

A better approach is to share appropriate learning summaries and academic reports.

For older students, the institution must define suitable access and consent rules. For minors, guardian access must follow the applicable consent and privacy framework.

14. Complete Academic Knowledge System
This is the foundation that connects the teacher, student, institution and AI systems.

VIORA should not simply store uploaded PDFs.

It should transform approved academic resources into structured knowledge.
14.1 Knowledge creation process
Teacher uploads academic resource.

↓

Original file is stored privately.

↓

File validation and security checks.

↓

Document parsing.

↓

OCR, where needed.

↓

Heading and section detection.

↓

Paragraph, table and formula extraction.

↓

Concept identification.

↓

Chapter and topic mapping.

↓

Syllabus mapping.

↓

Structured ContentUnits.

↓

Parent-child chunk creation.

↓

Embedding generation.

↓

Full-text indexing.

↓

Teacher review.

↓

Approval.

↓

Publication.

↓

Student retrieval and AI usage.
14.2 Structured knowledge relationships
For example:

Subject: Machine Learning

Chapter: Regression

Topic: Linear Regression

Concepts:
- Hypothesis function.
- Parameters.
- Cost function.
- Gradient descent.
- Model prediction.
Each concept can be associated with:
- Source documents.
- Page numbers.
- Teacher-approved explanations.
- Formulas.
- Examples.
- Questions.
- PYQs.
- Prerequisites.
- Related concepts.
- Learning evidence.
This creates a structured academic knowledge graph.
14.3 Knowledge trust levels
Each source and content item should have a trust status.
- Institution-approved.
- Teacher-verified.
- Partner-verified.
- Licensed.
- AI-generated draft.
- Student-uploaded.
- Awaiting review.
- Restricted.
- Expired or withdrawn.
Student-facing retrieval should follow the institution's publication and access rules.

15. Complete AI System Inside VIORA
VIORA will contain several AI capabilities, but they should be managed through a common AI infrastructure.
15.1 AI Tutor
Purpose: Answer academic questions.

Inputs:
- Student question.
- Current subject.
- Current topic.
- Institution.
- Approved resources.
- Conversation context within authorised scope.
Output:
- Explanation.
- Examples.
- Citations.
- Follow-up suggestions.
- Hints.
15.2 RAG Engine
Purpose: Retrieve reliable academic information.

It should use:
- Metadata filtering.
- Tenant filtering.
- Permission filtering.
- Approval filtering.
- Semantic retrieval.
- Keyword retrieval.
- Reranking when beneficial.
- Context sufficiency checks.
- Citation validation.
15.3 AI Content Generation
Purpose: Generate academic material.

Outputs:
- Notes.
- Summaries.
- Explanations.
- Formula sheets.
- Examples.
- Diagrams.
- Mind maps.
- Flashcards.
15.4 AI Question Generation
Purpose: Create practice-question drafts.

Checks:
- Correct answer.
- Syllabus mapping.
- Difficulty.
- Ambiguity.
- Duplicate detection.
- Concept mapping.
Teacher approval is required before formal assessments use generated questions.
15.5 Learning Intelligence
Purpose: Understand student learning evidence.

Tracks:
- Attempts.
- Correctness.
- Difficulty.
- Hint usage.
- Concept mapping.
- Repeated errors.
- Delayed revision results.
The platform should distinguish between:
- Unknown knowledge.
- Demonstrated understanding.
- Repeated difficulty.
- Improvement.
- Retained understanding.
A student who has not attempted a topic should not automatically be labelled weak.
15.6 Visual Learning Engine
Purpose: Generate visual explanations.

Process:
1. Retrieve approved knowledge.
2. Prepare lesson outline.
3. Generate scene structure.
4. Create visual elements.
5. Generate narration.
6. Generate captions.
7. Validate against the lesson outline.
8. Obtain teacher approval.
9. Publish.
This is an optional extension of the learning experience.

16. Separate LLM Evaluation Project — Complete Integration Relationship
This deserves its own architecture because you specifically want it as an independent project.
16.1 Project separation
Project 1: VIORA AI

Contains:
- All dashboards.
- Institution management.
- Academic resources.
- RAG.
- AI Tutor.
- Assessments.
- Learning analytics.
- Parent access.
- Billing.
Project 2: Independent LLM Evaluation Platform

Contains:
- Evaluation datasets.
- Evaluation test cases.
- Evaluation runners.
- Metrics.
- Model comparisons.
- Prompt comparisons.
- RAG evaluations.
- Tutor evaluations.
- Safety evaluations.
- Regression testing.
- Evaluation reports.
- Release quality gates.
16.2 Relationship between projects
VIORA AI sends selected evaluation tasks and events to the LLM Evaluation Platform.

The Evaluation Platform returns:
- Evaluation score.
- Evaluation status.
- Failed test cases.
- Quality metrics.
- Error categories.
- Report references.
VIORA stores the integration status and displays the relevant information in its internal dashboard.
16.3 Evaluation before production
Before releasing a new model or prompt:
1. Create a new model or prompt version.
2. Run the evaluation dataset.
3. Compare it with the current production version.
4. Check correctness.
5. Check retrieval quality.
6. Check citation validity.
7. Check safety.
8. Check cost and latency.
9. Review failures.
10. Approve or reject the release.
16.4 Evaluation after production
1. VIORA serves a student request.
2. The AI response is returned through the normal learning experience.
3. An evaluation event is recorded.
4. Selected interactions are sent for asynchronous evaluation.
5. The evaluation system analyses them.
6. Results are returned.
7. Quality trends appear in the internal dashboard.
8. The AI team investigates recurring issues.
9. Improved prompts or models are tested.
10. Approved changes are deployed.
For privacy, use data minimisation, anonymisation or pseudonymisation, appropriate retention and access controls. Do not indiscriminately copy all student conversations into the evaluation platform.
16.5 Why separate evaluation is better
- Independent development.
- Reusable across other AI projects.
- Easier to test.
- Easier to maintain.
- Independent model comparison.
- Cleaner main product architecture.
- Better release quality controls.
- Easier to scale evaluation independently.
- No technical evaluation interface exposed to students or teachers.
The main VIORA application should not depend on the evaluation service being available for every normal student response. Evaluation failure must not make the learning product unavailable.

17. Complete End-to-End User Workflows
Now all dashboards are connected.
Workflow A — New institution onboarding
1. Institution contacts VIORA.
2. VIORA sales team demonstrates the platform.
3. Institution chooses a pilot or subscription.
4. Super Admin creates the institution.
5. Subscription and seat limits are configured.
6. Institution Owner is invited.
7. Institution Admin is invited.
8. Admin creates departments.
9. Admin creates programmes.
10. Admin creates academic years and semesters.
11. Admin creates sections and classes.
12. Admin adds HODs.
13. Admin adds teachers.
14. Admin imports students.
15. Admin links parents, if enabled.
16. Academic packs are configured.
17. Institution is activated.
18. Users receive access.
Workflow B — Teacher prepares academic content
1. Teacher logs in.
2. Opens My Classes.
3. Selects a subject.
4. Selects a chapter.
5. Uploads notes.
6. VIORA validates the file.
7. The ingestion system extracts content.
8. The system structures topics and concepts.
9. RAG indexes the approved content.
10. Teacher reviews the extracted material.
11. Teacher corrects mistakes.
12. Teacher approves the material.
13. Teacher publishes it to selected classes.
14. Students receive access.
Workflow C — Student learns a topic
1. Student logs in.
2. Opens My Subjects.
3. Selects a subject.
4. Selects a chapter.
5. Opens a topic.
6. Reads the explanation.
7. Views notes and formulas.
8. Opens examples and diagrams.
9. Asks a doubt.
10. AI Tutor retrieves approved knowledge.
11. AI answers with supporting sources.
12. Student asks follow-up questions.
13. Student practises questions.
14. Learning evidence is recorded.
15. Student revisits the topic for revision.
Workflow D — Teacher conducts a test
1. Teacher selects a class.
2. Selects a subject.
3. Selects a topic.
4. Opens the question bank.
5. Selects verified questions.
6. Optionally generates new questions.
7. Reviews the questions and answers.
8. Sets duration and marks.
9. Selects all or specific students.
10. Publishes or schedules the test.
11. Students receive notifications.
12. Students attempt the test.
13. Objective responses are graded.
14. Results are calculated.
15. Teacher views class performance.
16. Concept-level gaps are identified.
17. Teacher assigns follow-up practice.
Workflow E — Parent monitors progress
1. Parent receives an invitation.
2. Parent verifies their account.
3. Institution confirms the guardian relationship.
4. Parent opens the linked child profile.
5. Views academic progress.
6. Views available subject reports.
7. Checks test results.
8. Sees upcoming assignments and announcements.
9. Receives authorised progress notifications.
10. Contacts the teacher when communication is enabled.
Workflow F — Institution analyses performance
1. Institution Admin opens Analytics.
2. Selects department.
3. Selects programme.
4. Selects academic year.
5. Reviews class-level performance.
6. Compares subject performance.
7. Identifies weak chapters and concepts.
8. Reviews teacher adoption.
9. Reviews student engagement.
10. Coordinates academic interventions.
11. Measures progress in subsequent assessments.
Workflow G — VIORA improves its AI quality
1. AI team updates a model or prompt.
2. New version is registered.
3. Evaluation platform runs the test suite.
4. Results are compared with the existing version.
5. Failed cases are reviewed.
6. Academic and technical reviewers inspect the results.
7. Release is approved or rejected.
8. Approved version is deployed.
9. Production quality is monitored.
10. The internal dashboard displays trends and alerts.

18. Complete Module and Dashboard Relationship Matrix
This is the high-level access relationship.
Module	Super Admin	Institution Admin	HOD	Teacher	Student	Parent
Global platform management	Full	No	No	No	No	No
Institution management	Platform-level	Full	Department scope	No	No	No
Department management	Platform support	Full	Assigned	No	No	No
Teacher management	Platform support	Full	Assigned department	No	No	No
Student management	Platform support	Full	Assigned scope	Assigned classes	Own profile	Linked child
Parent management	Platform support	Full	If authorised	If authorised	No	Own account
Syllabus	Global packs	Institution	Department	Assigned subjects	View	Limited view
Resource upload	Support access	Institution	Department	Assigned subjects	No	No
Resource publishing	No routine academic publishing	Policy control	If authorised	Assigned content	View	No
AI Tutor	Quality monitoring	Usage monitoring	Reports	Teacher tools	Use	No
Question bank	Global management	Institution oversight	Department	Assigned subjects	Practice	No
Tests	Platform oversight	Institution oversight	Department reports	Create and manage	Attempt	Results summary
Learning analytics	Platform-level aggregated	Institution	Department	Assigned classes	Own progress	Linked child summary
Billing	Full	Institution billing	No	No	No	No
LLM Evaluation	Full internal	No	No	No	No	No

Permissions must be configurable and must never be broader merely because a user has a familiar job title.

19. Technical Architecture — Complete System Components
19.1 Frontend
One role-aware React and TypeScript application.

It contains:
- Platform Super Admin workspace.
- Institution Owner workspace.
- Institution Admin workspace.
- HOD workspace.
- Teacher workspace.
- Student workspace.
- Parent workspace.
- Academic operations workspace.
Each role receives the correct navigation and access.
19.2 Main Backend
FastAPI modular backend.

Major modules:
1. Authentication.
2. User and role management.
3. Institution and tenant management.
4. Department management.
5. Programme and class management.
6. Student management.
7. Guardian relationships.
8. Syllabus and academic packs.
9. Resource management.
10. Document ingestion.
11. Knowledge management.
12. RAG retrieval.
13. AI Tutor.
14. AI Content Studio.
15. Question Bank.
16. PYQ Management.
17. Assessment Engine.
18. Learning Evidence.
19. Learning Analytics.
20. Revision Management.
21. Notifications.
22. Subscription and Billing.
23. AI Usage Ledger.
24. Audit and Governance.
25. Evaluation Integration.
19.3 Database
PostgreSQL stores:
- Users.
- Institutions.
- Roles.
- Permissions.
- Departments.
- Classes.
- Subjects.
- Syllabus.
- Resources.
- Content units.
- Questions.
- Assessments.
- Student attempts.
- Learning evidence.
- Parent relationships.
- Subscriptions.
- AI usage.
- Audit logs.
pgvector stores vector embeddings within the PostgreSQL environment.

Full-text search supports keyword-based retrieval.
19.4 File Storage
Private object storage stores:
- Uploaded PDFs.
- Documents.
- Images.
- Scanned resources.
- Generated lesson files.
- Approved visual content.
- Audio and video assets.
Access must be permission-controlled.
19.5 Background Processing
Workers process:
- Document parsing.
- OCR.
- Content structuring.
- Embedding creation.
- Indexing.
- Large content generation.
- Video generation.
- Report generation.
- Asynchronous evaluation events.
Redis can support caching and job coordination. Use a durable queue for jobs that must not disappear.
19.6 AI Model Gateway
All AI operations should pass through a common provider-independent gateway.

It manages:
- Model selection.
- Prompt versions.
- Fallback models.
- Token limits.
- Rate limits.
- Institution quotas.
- Cost calculation.
- Usage tracking.
- Request logs.
- Caching.
- AI provider health.
19.7 Independent LLM Evaluation Platform
This has its own:
- Backend.
- Evaluation database.
- Test datasets.
- Evaluation runners.
- Metrics.
- Report generation.
- Model comparisons.
- Integration API.
It communicates with VIORA through a controlled integration layer.

20. Important Database Relationships
These relationships are the foundation of the complete product.
Main entity	Relationship
Institution	Has many campuses and departments
Department	Belongs to an institution
Programme	Belongs to an institution or department
Academic Year	Belongs to an institution
Semester	Belongs to a programme/year structure
Class/Section	Belongs to a programme and academic period
Teacher	Has institutional membership and subject/class assignments
Student	Has institutional membership and class/subject enrolments
Parent	Has verified guardian relationships with one or more students
Subject	Belongs to an academic programme or pack
Chapter	Belongs to a subject
Topic	Belongs to a chapter
Concept	Belongs to a topic and may have prerequisite relationships
Resource	Belongs to an institution or licensed/shared pack
Content Unit	Is extracted from a resource
Chunk	Belongs to a content unit
Embedding	Represents an indexed chunk
Publication	Links approved content to a class or audience
Question	Maps to concepts and source information
PYQ	Maps to an examination, year, subject and source
Test	Belongs to a teacher/class and subject
Test Attempt	Belongs to a student and test version
Learning Evidence	Connects a student, concept and learning event
Mastery Estimate	Summarises evidence for a concept
Subscription	Belongs to an institution
Usage Ledger	Records AI consumption by institution and feature
Evaluation Dataset	Belongs to the independent evaluation platform
Evaluation Run	Executes a dataset against a model/prompt version
Evaluation Result	Stores quality metrics and failed cases

The system should preserve versioning for syllabus packs, resources, published tests, prompts and models.

21. Security, Privacy and Governance
This cannot be added at the end.

It must be part of the foundation.
21.1 Authentication
- Secure login.
- Password reset.
- Email verification where appropriate.
- Session management.
- Optional institutional SSO later.
- Secure account recovery.
21.2 Authorisation
- Role-based access control.
- Assignment-based access control.
- Institution-level data isolation.
- Department-level restrictions.
- Class-level restrictions.
- Student-level restrictions.
- Guardian relationship validation.
21.3 Tenant Isolation
Institution A must never retrieve Institution B's private data.

This must be enforced at both API and database levels.
21.4 Student Privacy
- Data minimisation.
- Consent management.
- Retention policies.
- Data export and deletion workflows.
- Appropriate guardian access.
- Restricted administrative access.
- Secure AI processing.
- Audit logs.
21.5 Academic Integrity
- Preserve original sources.
- Preserve source and licence information.
- Verify PYQ permissions.
- Review generated question answers.
- Keep test versions immutable after publication.
- Record grading corrections.
- Clearly distinguish AI-generated drafts from approved content.
21.6 AI Security
- Uploaded content is treated as data, not system instructions.
- Prompt injection checks.
- Strict retrieval permission filters.
- No cross-tenant context mixing.
- Citation verification.
- Output safety checks.
- AI usage limits.

22. Business Pricing, Costs and Financial Planning
A commercial model should be validated through actual institutional conversations and paid pilots.

Potential student-seat price experiments:
- ₹500 per student/year.
- ₹750 per student/year.
- ₹1,000 per student/year.
These are hypotheses, not confirmed market prices.

Example:

1,000 students × ₹750/year = ₹7.5 lakh annual gross seat revenue.

This is before deducting salaries, infrastructure, AI costs, onboarding, support, sales, payment charges and taxes.

A reasonable early business model is:

Institution subscription + student seats + included AI credits + additional usage.

The AI cost target should be measured as a percentage of collected revenue, not assumed. A 20–25% AI cost target can be used as an internal planning hypothesis and tested against real usage.

The financial dashboard should track:
- Revenue per institution.
- Revenue per active student.
- AI cost per active student.
- AI cost per feature.
- Infrastructure cost.
- Support cost.
- Onboarding cost.
- Contribution margin.
- Renewal rate.
- Customer acquisition cost.
The number of institutions needed for break-even depends on contribution margin, not just gross revenue.

23. Complete Development Roadmap
Build in the following order.
Phase 0 — Product and customer validation
- Select initial institution segment.
- Identify an actual pilot institution.
- Identify economic buyer.
- Identify teacher champion.
- Collect real sample academic materials.
- Validate pricing.
- Freeze initial subject and cohort.
Phase 1 — Platform Foundation
- Authentication.
- Institution workspaces.
- Tenant isolation.
- Roles and permissions.
- Organisation structure.
- Teacher/student management.
- Guardian relationship foundation.
- Syllabus framework.
Phase 2 — Resource Intelligence
- File upload.
- Private storage.
- Document validation.
- Parsing.
- OCR fallback.
- Content structuring.
- Syllabus mapping.
- RAG.
- Source citations.
- Teacher approval.
Phase 3 — Teacher Workflow
- Teacher dashboard.
- Subject management.
- Resource library.
- AI Content Studio.
- Publication.
- Question bank.
- Basic quiz creation.
- Basic class report.
- Concept-level accuracy.
The concept graph and question-to-concept mapping must be ready for useful concept reporting.
Phase 4 — Student Learning
- Student dashboard.
- Learn workspace.
- Notes.
- Explanations.
- Formulas.
- Examples.
- Diagrams.
- AI Tutor.
- Source-grounded answers.
Phase 5 — Practice and Assessment
- Practice workspace.
- PYQ bank.
- Test builder.
- Test scheduling.
- Student attempts.
- Objective grading.
- Results.
- Teacher reports.
Phase 6 — Learning Intelligence
- Learning evidence.
- Mistake classification.
- Mastery estimates.
- Revision recommendations.
- Student analytics.
- Class analytics.
- Teacher interventions.
Phase 7 — Parent Dashboard
- Guardian verification.
- Child linking.
- Parent overview.
- Progress reports.
- Notifications.
- Parent-teacher communication if enabled.
The parent account foundation can be designed earlier, but full parent-facing functionality can be introduced after the core student and teacher workflows work reliably.
Phase 8 — Independent LLM Evaluation Platform
Build and validate the evaluation system independently.
- Evaluation datasets.
- Evaluation runners.
- RAG evaluation.
- Tutor evaluation.
- Question generation evaluation.
- Safety evaluation.
- Model comparison.
- Prompt comparison.
- Regression tests.
- Evaluation reports.
Then integrate it with VIORA's backend and internal dashboard.

This can begin in parallel with the core AI work, but production evaluation requires real VIORA AI features and representative test data.
Phase 9 — Production and Commercial Expansion
- Security testing.
- Backup and restore testing.
- Performance testing.
- AI cost optimisation.
- Institution onboarding.
- Billing.
- Support workflows.
- Pilot measurement.
- Second institution.
- Second academic pack.
A practical end-to-end planning range is approximately 9–12 months for a lean build, depending on team capability, pilot readiness, scope and testing.

24. What Should Be in the First MVP?
Do not attempt to deliver all the dashboards and advanced features at once.

The first MVP should prove the main institutional learning loop.

Institution:
- Admin dashboard.
- Departments.
- Classes.
- Teachers.
- Students.
- Basic subscription and usage.
Teacher:
- Teacher dashboard.
- Subject and class assignment.
- Upload resources.
- Organise content.
- Review and publish.
- Question bank.
- Basic quiz creation.
- Class result and concept report.
Student:
- Student dashboard.
- Subjects and syllabus.
- Learn workspace.
- AI Tutor.
- Notes and explanations.
- Practice questions.
- Assigned tests.
- Basic progress.
VIORA Internal:
- Super Admin.
- Institution management.
- AI usage and cost.
- System health.
- Basic AI quality monitoring.
Independent Evaluation Project:
- Initial RAG evaluation dataset.
- Tutor evaluation.
- Citation validation.
- Question-generation evaluation.
- Regression testing.
- Controlled backend integration.
Parent:
- Guardian data relationships and permissions designed from the beginning.
- Parent-facing dashboard can follow after the main student and teacher flows are validated.

25. Final Product Structure — All Systems Connected
This is the complete system hierarchy.

VIORA AI
|
|-- A. Main Platform
|   |
|   |-- Platform Super Admin Dashboard
|   |-- Institution Owner Dashboard
|   |-- Institution Admin Dashboard
|   |-- HOD / Department Dashboard
|   |-- Class Teacher Dashboard
|   |-- Subject Teacher Dashboard
|   |-- Student Dashboard
|   |-- Parent / Guardian Dashboard
|   |-- Academic Content Operations Dashboard
|   |
|   |-- B. Academic System
|   |   |-- Institution Management
|   |   |-- Department Management
|   |   |-- Programme Management
|   |   |-- Class Management
|   |   |-- Subject Management
|   |   |-- Syllabus Management
|   |   |-- Resource Management
|   |   |-- Knowledge Management
|   |
|   |-- C. AI System
|   |   |-- AI Tutor
|   |   |-- RAG Engine
|   |   |-- AI Content Studio
|   |   |-- Question Generation
|   |   |-- Visual Learning
|   |   |-- Learning Intelligence
|   |
|   |-- D. Assessment System
|   |   |-- Question Bank
|   |   |-- PYQ Management
|   |   |-- Practice Engine
|   |   |-- Quiz Management
|   |   |-- Test Management
|   |   |-- Grading
|   |   |-- Results
|   |
|   |-- E. Learning Intelligence
|   |   |-- Student Progress
|   |   |-- Concept Performance
|   |   |-- Mistake Book
|   |   |-- Revision
|   |   |-- Teacher Interventions
|   |   |-- Parent Progress Reports
|   |
|   |-- F. Business and Governance
|       |-- Subscriptions
|       |-- Billing
|       |-- AI Usage Ledger
|       |-- Security
|       |-- Privacy
|       |-- Audit
|       |-- Support
|
|-- B. Independent LLM Evaluation Project
    |
    |-- Evaluation Datasets
    |-- Test Cases
    |-- RAG Evaluation
    |-- Tutor Evaluation
    |-- Question Evaluation
    |-- Content Evaluation
    |-- Safety Evaluation
    |-- Model Comparison
    |-- Prompt Comparison
    |-- Regression Testing
    |-- Evaluation Reports
    |-- Release Quality Gates
    |
    |-- Private Integration with VIORA Backend
    |
    |-- Results displayed in VIORA Internal AI Quality Dashboard

26. Final Product Positioning
VIORA AI should be built around this central concept:

An institution-powered AI learning ecosystem that transforms academic resources into trusted, personalised and measurable learning experiences.

The institution provides the academic structure and resources.

The HOD manages departmental academic activity.

The class coordinator manages class-level coordination.

The subject teacher manages content, teaching and assessments.

The student learns, asks doubts, practises and improves.

The parent receives authorised visibility into the child's progress.

The VIORA team manages the platform, business, security and AI infrastructure.

The separate LLM Evaluation Platform continuously measures and improves AI quality behind the scenes.

The key architecture decision is that VIORA is one integrated B2B2C product, supported by a separate reusable LLM Evaluation project. This gives you a clean main product, independent evaluation capabilities and the ability to reuse your evaluation system in future AI products.

This is the consolidated baseline to use for the next detailed requirements and implementation planning.