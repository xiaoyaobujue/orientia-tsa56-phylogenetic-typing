import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const apiSource = await readFile(new URL("../app/analysisApi.ts", import.meta.url), "utf8");
const workspaceSource = await readFile(new URL("../app/GenotypingWorkspace.tsx", import.meta.url), "utf8");

test("public client exposes only the strict article mode and tree endpoints", () => {
  assert.match(apiSource, /ARTICLE_MODE = "v1_strict_article"/);
  assert.doesNotMatch(apiSource, /\/api\/v2\//);
  assert.doesNotMatch(workspaceSource, /placement|快速分型/i);
});

test("result URL helper accepts API-provided relative download URLs", () => {
  assert.match(apiSource, /filename\.startsWith\("\/"\)/);
  assert.match(apiSource, /return `\$\{API_BASE_URL\}\$\{filename\}`/);
});

test("publication interface provides persistent English and Chinese modes", () => {
  assert.match(workspaceSource, /Orientia TSA56 Phylogenetic Typing/);
  assert.match(workspaceSource, /恙虫东方体 TSA56 系统发育分型/);
  assert.match(workspaceSource, /orientia-tsa56-locale/);
  assert.match(workspaceSource, /navigator\.language/);
  assert.match(workspaceSource, /aria-label="Language \/ 语言"/);
});
