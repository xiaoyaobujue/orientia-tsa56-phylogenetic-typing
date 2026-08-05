import assert from "node:assert/strict";
import test from "node:test";

import {
  autoSampleId,
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
