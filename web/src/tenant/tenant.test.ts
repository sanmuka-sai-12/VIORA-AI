import test from "node:test";
import assert from "node:assert/strict";
import {
  createApiClient,
  normalizeApiError,
  type InstitutionDiscovery,
} from "../api/client.ts";
import {
  buildNavigationItems,
  defaultRouteForRoles,
  deriveRoleFlags,
} from "./navigation.ts";
import { createTenantController } from "./tenantState.ts";
import {
  advanceCursorPage,
  createInitialPageState,
  isValidUuid,
  rewindCursorPage,
  validatePasswordChange,
  validateRequiredName,
  validateRequiredUuid,
} from "./validation.ts";

function buildTenantController(institutions: InstitutionDiscovery[]) {
  const mockFetch: typeof globalThis.fetch = async (input) => {
    const req = input instanceof Request ? input : new Request(String(input));
    const url = new URL(req.url);
    if (url.pathname === "/api/v1/institutions") {
      return new Response(JSON.stringify(institutions), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(null, { status: 404 });
  };

  return createTenantController({
    apiClient: createApiClient({
      baseUrl: "http://localhost:5173",
      getAuthToken: () => "tok",
      fetch: mockFetch,
    }),
    normalizeError: normalizeApiError,
  });
}

const TENANT_A = "11111111-1111-1111-1111-111111111111";
const TENANT_B = "22222222-2222-2222-2222-222222222222";

test("TenantController handles zero memberships cleanly", async () => {
  const ctrl = buildTenantController([]);
  await ctrl.discover();
  const snap = ctrl.getSnapshot();

  assert.equal(snap.status, "ready");
  assert.equal(snap.institutions.length, 0);
  assert.equal(snap.selectedTenantId, null);
  assert.equal(ctrl.selectInstitution(TENANT_A), false);
});

test("TenantController auto-selects when user holds exactly one institution membership", async () => {
  const ctrl = buildTenantController([
    { tenant_id: TENANT_A, name: "Northbridge College", roles: ["institution_owner"] },
  ]);
  await ctrl.discover();
  const snap = ctrl.getSnapshot();

  assert.equal(snap.status, "ready");
  assert.equal(snap.selectedTenantId, TENANT_A);
  assert.equal(snap.selectedInstitution?.name, "Northbridge College");
});

test("TenantController supports switching across multiple institutions and clears previous tenant data cache", async () => {
  const ctrl = buildTenantController([
    { tenant_id: TENANT_A, name: "Northbridge College", roles: ["institution_owner"] },
    { tenant_id: TENANT_B, name: "Southvale Institute", roles: ["subject_teacher"] },
  ]);
  await ctrl.discover();

  assert.equal(ctrl.getSnapshot().selectedTenantId, null);

  assert.equal(ctrl.selectInstitution(TENANT_A), true);
  ctrl.setCached("departments", [{ id: "d1", name: "Computer Science" }]);
  assert.ok(ctrl.getCached("departments"));
  const epochAfterFirstSelect = ctrl.getSnapshot().epoch;

  // Unknown tenant cannot be selected
  assert.equal(ctrl.selectInstitution("99999999-9999-9999-9999-999999999999"), false);

  // Switching to TENANT_B wipes cached data from TENANT_A and increments epoch
  assert.equal(ctrl.selectInstitution(TENANT_B), true);
  assert.equal(ctrl.getCached("departments"), undefined);
  assert.equal(ctrl.getSnapshot().selectedTenantId, TENANT_B);
  assert.equal(ctrl.getSnapshot().epoch, epochAfterFirstSelect + 1);

  // Full clear on logout removes institutions and cached records
  ctrl.clear();
  assert.equal(ctrl.getSnapshot().status, "idle");
  assert.equal(ctrl.getSnapshot().institutions.length, 0);
  assert.equal(ctrl.getSnapshot().selectedTenantId, null);
});

test("buildNavigationItems and defaultRouteForRoles provide clean role-specific navigation without placeholders or diagnostics", () => {
  const headNav = buildNavigationItems(["institution_admin"], true);
  assert.deepEqual(
    headNav.map((i) => i.route),
    ["head-students", "head-faculty", "head-academics", "head-permissions", "account-password"],
  );
  assert.equal(defaultRouteForRoles(["institution_admin"], true), "head-students");

  const teacherSubjectNav = buildNavigationItems(["subject_teacher"], true);
  assert.deepEqual(
    teacherSubjectNav.map((i) => i.route),
    ["teacher-subjects", "account-password"],
  );
  assert.equal(defaultRouteForRoles(["subject_teacher"], true), "teacher-subjects");

  const teacherCoordNav = buildNavigationItems(["class_coordinator"], true);
  assert.deepEqual(
    teacherCoordNav.map((i) => i.route),
    ["teacher-class", "account-password"],
  );
  assert.equal(defaultRouteForRoles(["class_coordinator"], true), "teacher-class");

  const teacherBothNav = buildNavigationItems(["class_coordinator", "subject_teacher"], true);
  assert.deepEqual(
    teacherBothNav.map((i) => i.route),
    ["teacher-class", "teacher-subjects", "account-password"],
  );

  const studentNav = buildNavigationItems(["student"], true);
  assert.deepEqual(
    studentNav.map((i) => i.route),
    ["student-learning", "institution", "account-password"],
  );
  assert.equal(defaultRouteForRoles(["student"], true), "student-learning");
  assert.equal(studentNav.some((i) => i.route === "guardians"), false);
  assert.equal(studentNav.some((i) => i.route === "health"), false);

  const guardianOnlyNav = buildNavigationItems(["guardian"], true);
  assert.deepEqual(
    guardianOnlyNav.map((i) => i.route),
    ["guardians", "account-password"],
  );
  assert.equal(defaultRouteForRoles(["guardian"], true), "guardians");

  const flags = deriveRoleFlags(["institution_owner", "department_head"]);
  assert.equal(flags.isOwner, true);
  assert.equal(flags.isAdmin, true);
  assert.equal(flags.isDepartmentHead, true);
  assert.equal(flags.isStudent, false);
});

test("Validation and pagination state helpers validate inputs and maintain cursor history", () => {
  assert.equal(isValidUuid(TENANT_A), true);
  assert.equal(isValidUuid("not-a-uuid"), false);

  assert.ok(validateRequiredName("   ", "Campus name"));
  assert.equal(validateRequiredName("Main Campus", "Campus name"), null);

  assert.ok(validateRequiredUuid("bad", "User ID"));
  assert.equal(validateRequiredUuid(TENANT_A, "User ID"), null);

  assert.ok(validatePasswordChange("oldpw12345678", "short", "short"));
  assert.ok(
    validatePasswordChange("oldpw12345678", "newlongpassword1", "mismatch12345678"),
  );
  assert.ok(
    validatePasswordChange("samepassword123", "samepassword123", "samepassword123"),
  );
  assert.equal(
    validatePasswordChange("oldpassword123", "brandnewpassword123", "brandnewpassword123"),
    null,
  );

  const p0 = createInitialPageState();
  const p1 = advanceCursorPage(p0, "cursor-1");
  assert.equal(p1.currentCursor, "cursor-1");
  assert.deepEqual(p1.history, [null]);

  const p2 = advanceCursorPage(p1, "cursor-2");
  assert.equal(p2.currentCursor, "cursor-2");
  assert.deepEqual(p2.history, [null, "cursor-1"]);

  const back1 = rewindCursorPage(p2);
  assert.equal(back1.currentCursor, "cursor-1");
  assert.deepEqual(back1.history, [null]);
});

import {
  clearLearningSession,
  formatSourcePageLabel,
  loadLearningSession,
  saveLearningSession,
  toActiveAttachmentRefs,
} from "../features/learning/learningSessionState.ts";
import {
  canOwnerStudentProcess,
  canTeacherManageSource,
  groundingTone,
  reviewTone,
} from "../features/sources/sourceHelpers.ts";

test("Source authorization and badge helpers enforce role boundaries", () => {
  assert.equal(
    canTeacherManageSource(true, { origin_kind: "teacher_resource", lifecycle_state: "active" }),
    true,
  );
  assert.equal(
    canTeacherManageSource(true, { origin_kind: "student_submission", lifecycle_state: "active" }),
    false,
  );
  assert.equal(
    canOwnerStudentProcess(
      "u1",
      { origin_kind: "student_submission", owner_user_id: "u1", lifecycle_state: "active" },
      { offering_id: "o1", tenant_id: TENANT_A, student_submissions_enabled: true, teachers_can_view_student_submissions: true, updated_at: "2026-01-01T00:00:00Z", updated_by_user_id: null },
    ),
    true,
  );
  assert.equal(
    canOwnerStudentProcess(
      "u1",
      { origin_kind: "student_submission", owner_user_id: "u1", lifecycle_state: "active" },
      { offering_id: "o1", tenant_id: TENANT_A, student_submissions_enabled: false, teachers_can_view_student_submissions: false, updated_at: "2026-01-01T00:00:00Z", updated_by_user_id: null },
    ),
    false,
  );
  assert.equal(groundingTone("source_supported"), "success");
  assert.equal(groundingTone("learning_request"), "warning");
  assert.equal(reviewTone("approved"), "success");
});

test("Personal learning conversation state preserves across route transitions, scopes to user & institution, and clears on sign-out", () => {
  const ctrl = buildTenantController([
    { tenant_id: TENANT_A, name: "Northbridge College", roles: ["student"] },
    { tenant_id: TENANT_B, name: "Southvale Institute", roles: ["student"] },
  ]);
  ctrl.selectInstitution(TENANT_A);

  saveLearningSession(ctrl, {
    userId: "user-1",
    tenantId: TENANT_A,
    mode: "explain",
    prompt: "Follow up draft",
    messages: [{ id: "m1", role: "user", content: "Explain chain rule" }],
    attachments: [
      {
        included: true,
        attachment: {
          attachment_id: "att-1",
          doc_sha256: "a".repeat(64),
          title: "calculus.pdf",
          original_filename: "calculus.pdf",
          kind: "pdf",
          input_format: "digital_pdf",
          status: "ready",
          status_message: "Extracted 2 pages.",
          page_count: 2,
          chunk_count: 3,
          char_count: 420,
          preview_excerpt: "Derivative notes...",
          created_at: "2026-11-28T00:00:00Z",
        },
      },
      {
        included: true,
        attachment: {
          attachment_id: "att-2",
          doc_sha256: "b".repeat(64),
          title: "blank.png",
          original_filename: "blank.png",
          kind: "image",
          input_format: "png",
          status: "unreadable",
          status_message: "No readable text found.",
          page_count: 1,
          chunk_count: 0,
          char_count: 0,
          preview_excerpt: null,
          created_at: "2026-11-28T00:00:00Z",
        },
      },
    ],
  });

  // Same user & tenant restores preserved conversation and active attachments
  const restored = loadLearningSession(ctrl, "user-1", TENANT_A);
  assert.equal(restored?.messages.length, 1);
  assert.equal(restored?.prompt, "Follow up draft");
  assert.equal(restored?.attachments.length, 2);
  assert.deepEqual(toActiveAttachmentRefs(restored!.attachments), [
    { attachment_id: "att-1", doc_sha256: "a".repeat(64) },
  ]);
  assert.equal(
    formatSourcePageLabel({
      attachment_id: "att-1",
      title: "calculus.pdf",
      page_start: 2,
      page_end: 2,
      section_title: null,
      excerpt: "text",
    }),
    "p. 2",
  );

  // Different user or tenant does not leak state or attachments
  assert.equal(loadLearningSession(ctrl, "user-2", TENANT_A), null);
  assert.equal(loadLearningSession(ctrl, "user-1", TENANT_B), null);

  // Explicit New Topic clears active conversation and attachments
  clearLearningSession(ctrl, "user-1", TENANT_A);
  assert.equal(loadLearningSession(ctrl, "user-1", TENANT_A)?.messages.length, 0);
  assert.equal(loadLearningSession(ctrl, "user-1", TENANT_A)?.attachments.length, 0);

  // Sign-out (ctrl.clear()) wipes cached conversation and attachments completely
  saveLearningSession(ctrl, {
    userId: "user-1",
    tenantId: TENANT_A,
    mode: "question",
    prompt: "",
    messages: [{ id: "m2", role: "user", content: "Active chat" }],
    attachments: [],
  });
  ctrl.clear();
  assert.equal(loadLearningSession(ctrl, "user-1", TENANT_A), null);
});
