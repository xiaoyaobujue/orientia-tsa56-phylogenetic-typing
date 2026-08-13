import assert from "node:assert/strict";
import test from "node:test";

import { prepareBatchSamples } from "../app/batchPreparation.ts";

test("a sample QC failure is recorded while later samples continue", async () => {
  const attempted = [];
  const failureSnapshots = [];
  const result = await prepareBatchSamples({
    samples: ["PE-329", "PE-357", "PE-360"].map((sampleId) => ({ sampleId, payload: sampleId })),
    prepare: async ({ sampleId }) => {
      attempted.push(sampleId);
      if (sampleId === "PE-357") throw new Error("QC failed");
      return `job-${sampleId}`;
    },
    describeFailure: (reason) => reason.message,
    shouldAbort: () => false,
    onFailuresChanged: (failures) => failureSnapshots.push(failures),
  });

  assert.deepEqual(attempted, ["PE-329", "PE-357", "PE-360"]);
  assert.deepEqual(result.preparedJobIds, ["job-PE-329", "job-PE-360"]);
  assert.deepEqual(result.failures, [{ sampleId: "PE-357", reason: "QC failed" }]);
  assert.deepEqual(failureSnapshots.at(-1), result.failures);
});

test("a batch-level failure still aborts immediately", async () => {
  const attempted = [];
  const serviceError = new Error("service unavailable");

  await assert.rejects(
    prepareBatchSamples({
      samples: ["A", "B", "C"].map((sampleId) => ({ sampleId, payload: sampleId })),
      prepare: async ({ sampleId }) => {
        attempted.push(sampleId);
        if (sampleId === "B") throw serviceError;
        return `job-${sampleId}`;
      },
      describeFailure: (reason) => reason.message,
      shouldAbort: (reason) => reason === serviceError,
    }),
    serviceError,
  );

  assert.deepEqual(attempted, ["A", "B"]);
});

test("all sample failures produce no prepared jobs for a joint tree", async () => {
  const result = await prepareBatchSamples({
    samples: ["A", "B"].map((sampleId) => ({ sampleId, payload: sampleId })),
    prepare: async ({ sampleId }) => { throw new Error(`${sampleId} failed QC`); },
    describeFailure: (reason) => reason.message,
    shouldAbort: () => false,
  });

  assert.deepEqual(result.preparedJobIds, []);
  assert.equal(result.failures.length, 2);
});
