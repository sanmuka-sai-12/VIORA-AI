import test from "node:test";
import assert from "node:assert/strict";
import { featureRegistry } from "./registry.ts";

test("featureRegistry contains required modular domain lanes", () => {
  const expectedFeatures = [
    "identity",
    "institutions",
    "sources",
    "tutor",
    "lessons",
    "assessments",
    "analytics",
  ];

  for (const feature of expectedFeatures) {
    assert.ok(feature in featureRegistry, `Missing feature lane: ${feature}`);
    assert.strictEqual(
      featureRegistry[feature as keyof typeof featureRegistry].enabled,
      false,
      `Feature ${feature} should be disabled by default`
    );
  }
});
