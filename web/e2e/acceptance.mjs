import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const BASE_URL = process.env.VIORA_BASE_URL ?? "http://127.0.0.1:5173";
const TEST_PASSWORD = process.env.VIORA_TEST_PASSWORD;
const SCREENSHOT_DIR = process.env.VIORA_SCREENSHOT_DIR || null;
const EXECUTABLE_PATH = process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined;

const OWNER_EMAIL = process.env.VIORA_OWNER_EMAIL ?? "owner@northbridge.example";
const ADMIN_EMAIL = process.env.VIORA_ADMIN_EMAIL ?? "admin@northbridge.example";
const TEACHER_EMAIL = process.env.VIORA_TEACHER_EMAIL ?? "teacher@northbridge.example";
const STUDENT_EMAIL = process.env.VIORA_STUDENT_EMAIL ?? "student@northbridge.example";
const SOUTHVALE_OWNER_EMAIL =
  process.env.VIORA_SOUTHVALE_OWNER_EMAIL ?? "owner@southvale.example";

if (!TEST_PASSWORD) {
  console.error("VIORA_TEST_PASSWORD environment variable is required.");
  process.exit(1);
}

if (SCREENSHOT_DIR) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

async function saveScreenshot(page, name) {
  if (!SCREENSHOT_DIR) return;
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, name),
    fullPage: true,
  });
}

async function assertNoTokenStorage(page, label) {
  const counts = await page.evaluate(() => ({
    local: window.localStorage.length,
    session: window.sessionStorage.length,
  }));
  assert.equal(counts.local, 0, `${label}: localStorage must remain empty`);
  assert.equal(counts.session, 0, `${label}: sessionStorage must remain empty`);
}

async function login(page, email, password) {
  await page.locator("#login-identifier").fill(email);
  await page.locator("#login-password").fill(password);
  await page
    .locator("#main-content")
    .getByRole("button", { name: "Sign in", exact: true })
    .click();
}

function resolveBrowserLaunchOptions() {
  if (EXECUTABLE_PATH) {
    return { executablePath: EXECUTABLE_PATH, headless: true };
  }
  const commonPaths = [
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ];
  const detected = commonPaths.find((candidate) => fs.existsSync(candidate));
  return detected ? { executablePath: detected, headless: true } : { headless: true };
}

async function main() {
  const browser = await chromium.launch(resolveBrowserLaunchOptions());
  const consoleErrors = [];
  const pageErrors = [];
  const server5xx = [];
  const refreshCalls = [];

  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
    });
    const page = await context.newPage();

    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const txt = msg.text();
        if (!txt.includes("status of 401") && !txt.includes("status of 403")) {
          consoleErrors.push(txt);
        }
      }
    });
    page.on("pageerror", (err) => pageErrors.push(String(err)));
    page.on("response", (res) => {
      if (res.status() >= 500) {
        server5xx.push(`${res.status()} ${res.url()}`);
      }
      if (res.url().includes("/api/v1/auth/refresh")) {
        refreshCalls.push(res.status());
      }
    });

    // 1. Unauthenticated redirect & invalid credentials
    await page.goto(`${BASE_URL}/memberships`, { waitUntil: "networkidle" });
    await page.locator("#login-identifier").waitFor({ state: "visible" });
    assert.ok(page.url().endsWith("/login"), `Expected /login, got ${page.url()}`);
    await assertNoTokenStorage(page, "unauthenticated");

    await login(page, OWNER_EMAIL, "WrongPassword12345!");
    await page.getByRole("alert").waitFor({ state: "visible" });
    await saveScreenshot(page, "01-login-invalid.png");
    console.log("PASS 1: Login redirect + invalid credentials alert");

    // 2. Owner login -> multi-institution picker -> Northbridge College
    await login(page, OWNER_EMAIL, TEST_PASSWORD);
    await page.getByRole("heading", { name: "Select an Institution" }).waitFor({ state: "visible" });
    await assertNoTokenStorage(page, "owner authenticated");
    await saveScreenshot(page, "02-institution-picker.png");

    await page
      .locator("section.viora-card", { hasText: "Northbridge College" })
      .getByRole("button", { name: "Open workspace" })
      .click();
    await page.getByRole("heading", { name: "Northbridge College", level: 1 }).waitFor({ state: "visible" });

    const nav = page.getByRole("navigation", { name: "Institutional navigation" });
    for (const navLabel of [
      "Campuses",
      "Departments & Heads",
      "Programmes",
      "Years & Semesters",
      "Class Sections",
      "Subjects",
      "Offerings & Enrolments",
      "Members & Roles",
      "Guardian Links",
    ]) {
      await nav.getByRole("button", { name: navLabel, exact: true }).waitFor({ state: "visible" });
    }
    assert.equal(await nav.getByRole("button", { name: /AI Tutor/ }).isDisabled(), true);
    console.log("PASS 2: Multi-institution picker + Owner navigation + Phase 2 disabled");

    // 3. Academic hierarchy creation, rename, archival
    const tempSuffix = Date.now().toString(36);
    const createdCampus = `E2E Campus ${tempSuffix}`;
    const renamedCampus = `E2E Annex ${tempSuffix}`;
    await nav.getByRole("button", { name: "Campuses", exact: true }).click();
    await page.getByText("Main Campus").waitFor({ state: "visible" });

    await page.locator("#campus-create-name").fill(createdCampus);
    await page.getByRole("button", { name: "Create campus" }).click();
    await page.getByText(createdCampus, { exact: true }).waitFor({ state: "visible" });

    await page
      .locator("tr", { hasText: createdCampus })
      .getByRole("button", { name: "Rename" })
      .click();
    await page.locator("#campus-edit-name").fill(renamedCampus);
    await page.getByRole("button", { name: "Save changes" }).click();
    await page.getByText(renamedCampus, { exact: true }).waitFor({ state: "visible" });

    await page
      .locator("tr", { hasText: renamedCampus })
      .getByRole("button", { name: "Archive" })
      .click();
    await page.getByRole("dialog").getByRole("button", { name: "Archive" }).click();
    await page.getByText(`Campus "${renamedCampus}" archived.`).waitFor({ state: "visible" });
    console.log("PASS 3: Academic hierarchy creation, rename, and archival");

    // 4. Session restoration after page reload
    await nav.getByRole("button", { name: "Departments & Heads", exact: true }).click();
    await page.getByText("Computer Science").waitFor({ state: "visible" });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "Select an Institution" }).waitFor({ state: "visible" });
    await assertNoTokenStorage(page, "after reload");
    await page
      .locator("section.viora-card", { hasText: "Northbridge College" })
      .getByRole("button", { name: "Open workspace" })
      .click();
    await page.getByText("Computer Science").waitFor({ state: "visible" });
    console.log("PASS 4: Session restored via HttpOnly refresh cookie after page reload");

    // 5. Teacher assignments & Student enrolments
    await nav.getByRole("button", { name: "Offerings & Enrolments", exact: true }).click();
    await page.getByText("CS301 Algorithms (CSE-A)").waitFor({ state: "visible" });

    const offRow = page.locator("tr", { hasText: "CS301 Algorithms (CSE-A)" });
    await offRow.getByRole("button", { name: "Teachers" }).click();
    await page.getByRole("dialog").getByText("Prof. Tara Teacher", { exact: true }).waitFor({ state: "visible" });
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();

    await offRow.getByRole("button", { name: "Enrolments" }).click();
    await page.getByRole("dialog").getByText("Sam Student", { exact: true }).waitFor({ state: "visible" });
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    await saveScreenshot(page, "03-offerings-admin.png");
    console.log("PASS 5: Teacher assignments and student enrolments verified");

    // 6. Institution switching & tenant state isolation
    const southId = await page
      .locator("#institution-switcher option", { hasText: "Southvale Institute" })
      .getAttribute("value");
    assert.ok(southId, "Southvale Institute option must exist");
    await page.locator("#institution-switcher").selectOption(southId);
    await page.getByRole("heading", { name: "Southvale Institute", level: 1 }).waitFor({ state: "visible" });
    assert.equal(await nav.getByRole("button", { name: "Members & Roles" }).count(), 0);
    await nav.getByRole("button", { name: "Subject Offerings", exact: true }).click();
    await page.getByText("PHY101 Quantum (PHY-1)").waitFor({ state: "visible" });
    assert.equal(await page.getByText("CS301 Algorithms (CSE-A)").count(), 0);
    await saveScreenshot(page, "04-switched-southvale-teacher.png");
    console.log("PASS 6: Institution switch clears Northbridge state and enforces role nav");

    // 7. Expired-session handling (no retry loop)
    const callsBeforeExpiry = refreshCalls.length;
    await context.clearCookies();
    await page.reload({ waitUntil: "networkidle" });
    await page.locator("#login-identifier").waitFor({ state: "visible" });
    assert.equal(refreshCalls.length, callsBeforeExpiry + 1, "Refresh must not retry automatically");
    console.log("PASS 7: Expired/missing refresh cookie settles cleanly on /login");

    // 8. Admin auto-select, single-org reload, & logout
    await login(page, ADMIN_EMAIL, TEST_PASSWORD);
    await page.getByRole("heading", { name: "Northbridge College", level: 1 }).waitFor({ state: "visible" });
    await nav.getByRole("button", { name: "Departments & Heads", exact: true }).click();
    await page.getByText("Computer Science").waitFor({ state: "visible" });
    await page.reload({ waitUntil: "networkidle" });
    await page.getByText("Computer Science").waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.locator("#login-identifier").waitFor({ state: "visible" });
    console.log("PASS 8: Admin auto-select + single-org reload + Logout verified");

    // 9. Teacher restrictions & 403 handling
    await login(page, TEACHER_EMAIL, TEST_PASSWORD);
    await page.getByRole("heading", { name: "Northbridge College", level: 1 }).waitFor({ state: "visible" });
    assert.equal(await nav.getByRole("button", { name: "Campuses" }).count(), 0);
    await nav.getByRole("button", { name: "Subject Offerings", exact: true }).click();
    await page.getByText("CS301 Algorithms (CSE-A)").waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: "Create offering" }).count(), 0);

    await page.goto(`${BASE_URL}/memberships`, { waitUntil: "networkidle" });
    await page.getByText("Could not load members").waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.locator("#login-identifier").waitFor({ state: "visible" });
    console.log("PASS 9: Teacher scope and backend 403 denial UI verified");

    // 10. Student restrictions & Guardian policy UI
    await login(page, STUDENT_EMAIL, TEST_PASSWORD);
    await page.getByRole("heading", { name: "Northbridge College", level: 1 }).waitFor({ state: "visible" });
    await nav.getByRole("button", { name: "My Enrolled Offerings", exact: true }).click();
    await page.getByText("CS301 Algorithms (CSE-A)").waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: "Enrolments" }).count(), 0);

    await nav.getByRole("button", { name: "Guardian Consent", exact: true }).click();
    await page.getByText("Sam Student").waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: /Verify/i }).count(), 0);
    assert.equal(await page.getByRole("button", { name: /Activate/i }).count(), 0);
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.locator("#login-identifier").waitFor({ state: "visible" });
    console.log("PASS 10: Student restrictions & Guardian policy UI verified");

    // 11. Cross-tenant isolation + mobile responsive layout (dynamic header offset check)
    await login(page, SOUTHVALE_OWNER_EMAIL, TEST_PASSWORD);
    await page.getByRole("heading", { name: "Southvale Institute", level: 1 }).waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#institution-switcher option", { hasText: "Northbridge College" }).count(),
      0,
    );

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Toggle navigation menu" }).click();
    await nav.getByRole("button", { name: "Campuses", exact: true }).waitFor({ state: "visible" });
    await page.waitForTimeout(300);

    // Confirm mobile drawer starts at or below the bottom of the dynamically sized header
    const geometry = await page.evaluate(() => {
      const header = document.querySelector(".viora-header")?.getBoundingClientRect();
      const sidebar = document.querySelector(".viora-sidebar")?.getBoundingClientRect();
      return {
        headerBottom: header?.bottom ?? 0,
        sidebarTop: sidebar?.top ?? 0,
        sidebarLeft: sidebar?.left ?? -1,
        overflow: document.documentElement.scrollWidth > window.innerWidth + 2,
      };
    });
    assert.ok(
      geometry.sidebarTop >= geometry.headerBottom - 1,
      `Mobile sidebar (${geometry.sidebarTop}px) overlaps header (${geometry.headerBottom}px)`,
    );
    assert.equal(geometry.sidebarLeft, 0, "Mobile sidebar must align flush at left edge");
    assert.equal(geometry.overflow, false, "Mobile viewport must not horizontally overflow");

    await saveScreenshot(page, "05-mobile-responsive.png");
    console.log("PASS 11: Cross-tenant isolation + mobile responsive drawer verified");

    assert.deepEqual(consoleErrors, [], `Console errors: ${consoleErrors.join("; ")}`);
    assert.deepEqual(pageErrors, [], `Page errors: ${pageErrors.join("; ")}`);
    assert.deepEqual(server5xx, [], `5xx responses: ${server5xx.join("; ")}`);
    console.log("ALL 11 E2E ACCEPTANCE CHECKS PASSED!");
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
