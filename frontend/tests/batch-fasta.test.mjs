import assert from "node:assert/strict";
import test from "node:test";

import { combineBatchConsensusFasta } from "../app/batchFasta.ts";

test("combines only final sample consensus sequences into one FASTA", () => {
  const combined = combineBatchConsensusFasta([
    { sampleId: "SIM330-1", fastaText: ">old_header\nACGT\nNNAA\n" },
    { sampleId: "SIM334-2", fastaText: ">another_header\nTTGC\n" },
  ]);

  assert.equal(combined, ">SIM330-1\nACGTNNAA\n>SIM334-2\nTTGC\n");
  assert.doesNotMatch(combined, /old_header|another_header|reference/i);
});

test("rejects a completed sample with an empty consensus FASTA", () => {
  assert.throws(
    () => combineBatchConsensusFasta([{ sampleId: "SIM330-3", fastaText: ">SIM330-3\n" }]),
    /SIM330-3 的质控拼接序列为空/,
  );
});
