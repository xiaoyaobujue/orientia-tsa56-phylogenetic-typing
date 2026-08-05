import assert from "node:assert/strict";
import test from "node:test";

import { collectDroppedEntryFiles } from "../app/droppedFiles.ts";

function fileEntry(file) {
  return {
    isFile: true,
    isDirectory: false,
    file(resolve) {
      resolve(file);
    },
  };
}

function directoryEntry(batches) {
  return {
    isFile: false,
    isDirectory: true,
    createReader() {
      let index = 0;
      return {
        readEntries(resolve) {
          resolve(batches[index++] ?? []);
        },
      };
    },
  };
}

test("recursively reads all files from a dropped folder", async () => {
  const forward = new File(["forward"], "SIM-1_OT-3-NF.ab1");
  const reverse = new File(["reverse"], "SIM-1_OT-3-NR.ab1");
  const nested = new File(["nested"], "SIM-2_OT-NF.ab1");

  const childFolder = directoryEntry([
    [fileEntry(nested)],
    [],
  ]);
  const rootFolder = directoryEntry([
    [fileEntry(forward), childFolder],
    [fileEntry(reverse)],
    [],
  ]);

  const files = await collectDroppedEntryFiles(rootFolder);

  assert.deepEqual(
    files.map((file) => file.name),
    ["SIM-1_OT-3-NF.ab1", "SIM-2_OT-NF.ab1", "SIM-1_OT-3-NR.ab1"],
  );
});
