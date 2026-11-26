import test from "node:test";
import assert from "node:assert/strict";
import {
  createApiClient,
  normalizeApiError,
  type InstitutionDiscovery,
} from "../api/client.ts";
import { buildNavigationItems, deriveRoleFlags } from "./navigation.ts";
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

test("buildNavigationItems tailors navigation per role and keeps Phase 2 modules disabled", () => {
  const adminNav = buildNavigationItems(["institution_admin"], true);
  assert.ok(adminNav.some((i) => i.route === "memberships"));
  assert.ok(adminNav.some((i) => i.route === "academics-campuses"));

  const teacherNav = buildNavigationItems(["subject_teacher"], true);
  assert.ok(teacherNav.some((i) => i.route === "academics-offerings"));
  assert.ok(teacherNav.some((i) => i.route === "sources"));
  assert.equal(teacherNav.some((i) => i.route === "memberships"), false);
  assert.equal(teacherNav.some((i) => i.route === "academics-campuses"), false);

  const studentNav = buildNavigationItems(["student"], true);
  assert.ok(studentNav.some((i) => i.route === "academics-offerings"));
  assert.ok(studentNav.some((i) => i.route === "sources"));
  assert.ok(studentNav.some((i) => i.route === "guardians"));

  const guardianNav = buildNavigationItems(["guardian"], true);
  assert.ok(guardianNav.some((i) => i.route === "guardians"));
  assert.equal(guardianNav.some((i) => i.route === "academics-offerings"), false);
  assert.equal(guardianNav.some((i) => i.route === "sources"), false);

  const futureModules = adminNav.filter((i) => i.section === "future");
  assert.equal(futureModules.length, 4);
  for (const mod of futureModules) {
    assert.equal(mod.disabled, true);
  }

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
