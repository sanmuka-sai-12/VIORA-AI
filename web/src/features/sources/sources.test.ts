import test from "node:test";
import assert from "node:assert/strict";
import {
  createApiClient,
  type KnowledgePublicationState,
  type KnowledgeQAResult,
  type LessonBlueprintRecord,
} from "../../api/client.ts";
import {
  buildKnowledgeQABody,
  canApproveLessonBlueprint,
  canTeacherManageSource,
  canTriggerLessonBlueprint,
  formatInterpretationLabel,
  hasExposedAnswerKeyFields,
  qaStatusTone,
} from "./sourceHelpers.ts";

const activeTeacherSource = {
  origin_kind: "teacher_resource",
  lifecycle_state: "active",
} as const;

const approvedPubState = {
  teacher_approved: true,
  published_to_students: true,
} as unknown as KnowledgePublicationState;

const pendingPubState = {
  teacher_approved: false,
  published_to_students: false,
} as unknown as KnowledgePublicationState;

test("teacher blueprint permissions require active teacher_resource and approved curriculum", () => {
  assert.equal(canTeacherManageSource(true, activeTeacherSource), true);
  assert.equal(canTeacherManageSource(false, activeTeacherSource), false);
  assert.equal(
    canTriggerLessonBlueprint(true, activeTeacherSource, approvedPubState),
    true,
  );
  assert.equal(
    canTriggerLessonBlueprint(true, activeTeacherSource, pendingPubState),
    false,
  );
  assert.equal(
    canTriggerLessonBlueprint(false, activeTeacherSource, approvedPubState),
    false,
  );
  assert.equal(
    canApproveLessonBlueprint(true, activeTeacherSource, approvedPubState, null),
    false,
  );
  assert.equal(
    canApproveLessonBlueprint(
      true,
      activeTeacherSource,
      approvedPubState,
      { blueprint_id: "bp-1" } as unknown as LessonBlueprintRecord,
    ),
    true,
  );
});

test("QA request builder, status tones, interpretation labels, and answer-key isolation", () => {
  const body = buildKnowledgeQABody({
    question: "  What is the worst-case runtime of binary search? ",
    offeringId: "off-1",
    mode: "hybrid_graph",
    allowInterpretation: true,
    allowGeneralKnowledge: false,
    publishedOnly: true,
  });
  assert.equal(body.question, "What is the worst-case runtime of binary search?");
  assert.equal(body.offering_id, "off-1");
  assert.equal(body.allow_interpretation, true);
  assert.equal(body.allow_general_knowledge, false);
  assert.equal(body.published_only, true);
  assert.equal(body.include_student_submissions, false);

  assert.equal(qaStatusTone("answered"), "success");
  assert.equal(qaStatusTone("interpreted" as KnowledgeQAResult["status"]), "brand");
  assert.equal(qaStatusTone("clarification_needed"), "warning");
  assert.equal(qaStatusTone("insufficient_evidence"), "danger");

  assert.equal(
    formatInterpretationLabel("syllabus_request"),
    "Syllabus Learning Request (Non-Evidentiary)",
  );
  assert.equal(
    hasExposedAnswerKeyFields({ question_id: "q1", stem: "Q?", options: ["A", "B"] }),
    false,
  );
  assert.equal(
    hasExposedAnswerKeyFields({ question_id: "q1", correct_answer: "A" }),
    true,
  );
  assert.equal(
    hasExposedAnswerKeyFields({ question_id: "q1", correct_option_index: 0 }),
    true,
  );
});

test("Phase 3 typed API client routes index, blueprint approval, and QA requests cleanly", async () => {
  const requests: Array<{ method: string; url: string; body: string | null }> = [];
  const client = createApiClient({
    baseUrl: "http://localhost:5173",
    getAuthToken: () => "tok-123",
    fetch: async (input, init) => {
      const req = input instanceof Request ? input : new Request(String(input), init);
      requests.push({
        method: req.method,
        url: new URL(req.url).pathname,
        body: req.method === "GET" ? null : await req.clone().text(),
      });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.POST("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/index", {
    params: { path: { tenant_id: "t1", source_id: "s1", version_id: "v1" } },
  });
  await client.POST("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/review", {
    params: { path: { tenant_id: "t1", source_id: "s1", version_id: "v1" } },
    body: { blueprint_id: "bp-1", decision: "approved", publish: true, review_notes: "LGTM" },
  });
  await client.GET("/api/v1/tenants/{tenant_id}/sources/{source_id}/versions/{version_id}/blueprint/published", {
    params: { path: { tenant_id: "t1", source_id: "s1", version_id: "v1" } },
  });
  await client.POST("/api/v1/tenants/{tenant_id}/knowledge/qa", {
    params: { path: { tenant_id: "t1" } },
    body: buildKnowledgeQABody({
      question: "Explain binary search invariants.",
      offeringId: "off-1",
      mode: "hybrid_graph",
      allowInterpretation: true,
      allowGeneralKnowledge: false,
      publishedOnly: true,
    }),
  });

  assert.equal(requests.length, 4);
  assert.deepEqual(
    requests.map((r) => `${r.method} ${r.url}`),
    [
      "POST /api/v1/tenants/t1/sources/s1/versions/v1/index",
      "POST /api/v1/tenants/t1/sources/s1/versions/v1/blueprint/review",
      "GET /api/v1/tenants/t1/sources/s1/versions/v1/blueprint/published",
      "POST /api/v1/tenants/t1/knowledge/qa",
    ],
  );
  const qaPayload = JSON.parse(requests[3].body ?? "{}");
  assert.equal(qaPayload.allow_interpretation, true);
  assert.equal(qaPayload.published_only, true);
});
