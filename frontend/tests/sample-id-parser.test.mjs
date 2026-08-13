import assert from "node:assert/strict";
import test from "node:test";

import {
  autoSampleId,
  groupBatchFiles,
} from "../app/sampleIdParser.ts";

test("extracts the same sample ID from OT and OT-3 reads", () => {
  assert.equal(autoSampleId("SIM-REF-337_OT-NF_run.ab1"), "SIM-REF-337");
  assert.equal(autoSampleId("SIM-REF-337_OT-3-NR_run.ab1"), "SIM-REF-337");
});

test("preserves sample IDs that themselves begin with OT", () => {
  const forward = "SIM-REF-1_OT-3-NF_RUN_F01.ab1";
  const reverse = "SIM-REF-1_OT-3-NR_RUN_H01.ab1";

  assert.equal(autoSampleId(forward), "SIM-REF-1");
  assert.equal(autoSampleId(reverse), "SIM-REF-1");
});

test("does not strip the former 56kDa marker as an OT sample marker", () => {
  assert.equal(autoSampleId("SIM-REF-337_56kDa-F_run.ab1"), "SIM-REF-337_56kDa");
});

test("extracts numbered sample IDs from instrument plate replicate names", () => {
  const cases = [
    ["SIM330_RUN_1_F08.1_1.ab1", "SIM330-1"],
    ["SIM330_RUN_2_F08.2_1.ab1", "SIM330-2"],
    ["SIM330_RUN_3_F08.3_1.ab1", "SIM330-3"],
    ["SIM334_RUN_1_G08.1_1.ab1", "SIM334-1"],
    ["SIM334_RUN_2_G08.2_1.ab1", "SIM334-2"],
    ["SIM334_RUN_3_G08.3_1.ab1", "SIM334-3"],
  ];

  for (const [fileName, expected] of cases) {
    assert.equal(autoSampleId(fileName), expected);
  }
});

test("groups paired read numbers under the same numbered sample ID", () => {
  const read1 = "SIM330_RUN_1_F08.1_1.ab1";
  const read2 = "SIM330_RUN_1_F08.1_2.ab1";

  assert.equal(autoSampleId(read1), "SIM330-1");
  assert.equal(autoSampleId(read2), "SIM330-1");
});

test("splits repeat sequencing runs for the same sample into numbered samples", () => {
  const names = [
    "132-0627_OT-NF_TSS20260709-0871-01007-01_A09.ab1",
    "132-0627_OT-NF_TSS20260718-0871-01899_G04.ab1",
    "132-0627_OT-NR_TSS20260709-0871-01007-01_B09.ab1",
    "132-0627_OT-NR_TSS20260718-0871-01899_G05.ab1",
  ];

  const groups = groupBatchFiles(names.map((name) => ({ name })));

  assert.deepEqual(groups.map(([id]) => id), ["132-0627-1", "132-0627-2"]);
  assert.deepEqual(groups[0][1].map(({ name }) => name), [names[0], names[2]]);
  assert.deepEqual(groups[1][1].map(({ name }) => name), [names[1], names[3]]);
});

test("keeps an ordinary forward and reverse pair under its original sample ID", () => {
  const names = [
    "P-356_OT-NF_TSS20260709-0871-01007-01_E09.ab1",
    "P-356_OT-NR_TSS20260709-0871-01007-01_A10.ab1",
  ];

  const groups = groupBatchFiles(names.map((name) => ({ name })));

  assert.deepEqual(groups.map(([id]) => id), ["P-356"]);
  assert.equal(groups[0][1].length, 2);
});

test("does not hide a true overfilled group when run names cannot separate it", () => {
  const files = ["A01", "B01", "C01"].map((well) => ({
    name: `sample_OT-NF_TSS20260709-0871-01007-01_${well}.ab1`,
  }));

  const groups = groupBatchFiles(files);

  assert.deepEqual(groups.map(([id]) => id), ["sample"]);
  assert.equal(groups[0][1].length, 3);
});
