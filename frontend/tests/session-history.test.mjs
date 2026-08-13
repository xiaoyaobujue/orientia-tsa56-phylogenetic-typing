import assert from "node:assert/strict";
import test from "node:test";

import {
  parseSessionHistory,
  SESSION_HISTORY_LIMIT,
  updateSessionHistoryStatus,
  upsertSessionHistory,
} from "../app/sessionHistory.ts";

const entry = (suffix, overrides = {}) => ({
  jobId: suffix.repeat(32),
  label: `sample-${suffix}`,
  mode: "single",
  submittedAt: 1_700_000_000_000,
  status: "queued",
  ...overrides,
});

test("session history rejects malformed persisted data", () => {
  assert.deepEqual(parseSessionHistory("not-json"), []);
  assert.deepEqual(parseSessionHistory(JSON.stringify([{ jobId: "../bad" }])), []);
});

test("session history keeps the newest version of a job at the front", () => {
  const current = [entry("a"), entry("b")];
  const updated = upsertSessionHistory(current, entry("b", { status: "completed" }));

  assert.deepEqual(updated.map((item) => item.jobId), ["b".repeat(32), "a".repeat(32)]);
  assert.equal(updated[0].status, "completed");
});

test("session history is bounded and status updates preserve metadata", () => {
  const current = Array.from({ length: SESSION_HISTORY_LIMIT }, (_, index) =>
    entry((index % 10).toString(), { jobId: index.toString(16).padStart(32, "0") }),
  );
  const next = upsertSessionHistory(current, entry("f"));
  assert.equal(next.length, SESSION_HISTORY_LIMIT);

  const updated = updateSessionHistoryStatus(next, "f".repeat(32), "running");
  assert.equal(updated[0].status, "running");
  assert.equal(updated[0].label, "sample-f");
});
