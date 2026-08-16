import assert from "node:assert/strict";
import test from "node:test";

import { buildTypingResultsCsv } from "../app/typingResultsCsv.ts";

const columns = {
  sequence: "序列",
  newGenotype: "新基因型",
  oldGenotype: "旧基因型",
  newGroup: "新分组",
  oldGroup: "旧分组",
  support: "基因型锚定支 UFBoot",
  status: "判定",
};

test("exports the complete typing table as Excel-friendly UTF-8 CSV", () => {
  const csv = buildTypingResultsCsv([
    {
      sample_id: "060-0703",
      predicted_new_genotype: "5e",
      predicted_old_genotype: "Karp_A",
      predicted_new_group: "Group 5",
      predicted_old_group: "Karp",
      support: 0.83,
      status: "review_low_support",
    },
  ], columns, () => "支持度不足，需人工复核");

  assert.ok(csv.startsWith("\uFEFF"));
  assert.match(csv, /"序列","新基因型","旧基因型","新分组","旧分组","基因型锚定支 UFBoot","判定"/);
  assert.match(csv, /"060-0703","5e","Karp_A","Group 5","Karp","83%","支持度不足，需人工复核"/);
  assert.ok(csv.endsWith("\r\n"));
});

test("neutralizes spreadsheet formulas in exported cells", () => {
  const csv = buildTypingResultsCsv([
    { sample_id: "=HYPERLINK(\"bad\")", status: "matched" },
  ], columns, () => "支持分型");

  assert.match(csv, /"'=HYPERLINK\(""bad""\)"/);
});
