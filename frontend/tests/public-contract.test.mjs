import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const apiSource = await readFile(new URL("../app/analysisApi.ts", import.meta.url), "utf8");
const workspaceSource = await readFile(new URL("../app/GenotypingWorkspace.tsx", import.meta.url), "utf8");
const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");

test("public client exposes only the strict article mode and tree endpoints", () => {
  assert.match(apiSource, /ARTICLE_MODE = "v1_strict_article"/);
  assert.doesNotMatch(apiSource, /\/api\/v2\//);
  assert.doesNotMatch(workspaceSource, /placement|快速分型/i);
});

test("remote browsers use the same-origin API route", () => {
  assert.match(apiSource, /isRemoteBrowser \? ""/);
  assert.match(apiSource, /`\$\{API_BASE_URL\}\/api\/health`/);
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

test("methods and citation links open the matching language documentation", () => {
  assert.match(workspaceSource, /locale === "zh" \? "docs\/zh-CN" : "docs"/);
  assert.match(workspaceSource, /\/METHODS\.md/);
  assert.match(workspaceSource, /\/CITATION\.md/);
  assert.doesNotMatch(workspaceSource, /href="#(?:workflow|citation)"/);
});

test("input area accepts dropped files and folders in every analysis mode", () => {
  assert.match(workspaceSource, /collectDroppedFiles/);
  assert.match(workspaceSource, /onDragEnter=\{onDragEnter\}/);
  assert.match(workspaceSource, /onDrop=\{onDrop\}/);
  assert.match(workspaceSource, /folderInput\.setAttribute\("webkitdirectory", ""\)/);
  assert.match(workspaceSource, /chooseFolder/);
});

test("result view shows legacy typing and explains the strict support rule", () => {
  assert.match(apiSource, /predicted_old_genotype/);
  assert.match(apiSource, /predicted_old_group/);
  assert.match(workspaceSource, /row\.predicted_old_genotype/);
  assert.match(workspaceSource, /row\.predicted_old_group/);
  assert.match(workspaceSource, /review_low_support: "支持度不足，需人工复核"/);
  assert.match(workspaceSource, /完整参考基因型支系的边界支持度/);
  assert.match(workspaceSource, /自动接受阈值为 ≥95%/);
});

test("batch QC failures are isolated and shown without stopping valid samples", () => {
  assert.match(workspaceSource, /prepareBatchSamples/);
  assert.match(workspaceSource, /prepared\.preparedJobIds\.length === 0/);
  assert.match(workspaceSource, /质控失败并排除的样本/);
  assert.match(workspaceSource, /其余合格样本继续联合建树/);
});

test("the workbench keeps a browser-session result history and can restore a job", () => {
  assert.match(workspaceSource, /SESSION_HISTORY_KEY/);
  assert.match(workspaceSource, /rememberSubmittedJob/);
  assert.match(workspaceSource, /restoreHistoryResult/);
  assert.match(workspaceSource, /本次登录历史提交结果/);
  assert.match(workspaceSource, /clearCurrentResult/);
});

test("the batch preview opens a complete sample list", () => {
  assert.match(workspaceSource, /setSampleListOpen\(true\)/);
  assert.match(workspaceSource, /完整样本列表/);
  assert.match(workspaceSource, /batchGroups\.map\(\(\[id, group\], index\)/);
});

test("the phylogenetic tree uses wheel zoom, drag panning and an expanded viewer", () => {
  assert.match(workspaceSource, /clampTreeZoom/);
  assert.match(workspaceSource, /onTreeWheel/);
  assert.match(workspaceSource, /passive: false/);
  assert.match(workspaceSource, /onTreePointerDown/);
  assert.match(workspaceSource, /setPointerCapture/);
  assert.match(workspaceSource, /scrollLeft/);
  assert.match(workspaceSource, /鼠标滚轮缩放 · 按住左键拖拽移动/);
  assert.doesNotMatch(workspaceSource, /type="range"/);
  assert.match(workspaceSource, /setTreeExpanded\(true\)/);
  assert.match(workspaceSource, /tree-viewport is-expanded/);
});

test("the browser tab explicitly uses the analysis platform logo", () => {
  assert.match(layoutSource, /rel="icon" href="\/favicon\.svg\?v=1\.1\.0"/);
  assert.match(layoutSource, /rel="shortcut icon" href="\/favicon\.svg\?v=1\.1\.0"/);
  assert.match(layoutSource, /type="image\/svg\+xml"/);
});

test("input validation errors retain the server reason", () => {
  assert.match(workspaceSource, /服务端原因：\$\{reason\.message\}/);
});

test("the typing result table can be exported as CSV", () => {
  assert.match(workspaceSource, /buildTypingResultsCsv/);
  assert.match(workspaceSource, /导出 CSV/);
  assert.match(workspaceSource, /text\/csv;charset=utf-8/);
  assert.match(workspaceSource, /tsa56_typing_results_/);
});
