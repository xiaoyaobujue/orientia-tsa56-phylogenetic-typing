export function fileStem(fileName: string) {
  return fileName.replace(/\.[^.]+$/, "");
}

export function autoSampleId(fileName: string) {
  const stem = fileStem(fileName);
  const numberedPlateSample =
    /^([A-Za-z][A-Za-z0-9-]*\d+)_.*_[A-P]\d{1,3}\.(\d+)(?:_(?:\d+|nf|nr|f|r|forward|reverse))?$/i.exec(stem);
  if (numberedPlateSample) {
    const [, baseId, replicate] = numberedPlateSample;
    return `${baseId}-${Number(replicate)}`;
  }

  const targetMarker =
    /[_-]ot(?:[-_]?3)?[-_]?(?:nf|nr|f|r|forward|reverse)(?=[_-]|$)/i;
  const markerMatch = targetMarker.exec(stem);
  if (markerMatch?.index !== undefined) {
    return stem.slice(0, markerMatch.index).replace(/[_-]+$/, "");
  }

  return stem
    .replace(/[_-](?:nf|nr|f|r|forward|reverse)(?=[_-]|$).*$/i, "")
    .replace(/[_-]+$/, "");
}

function sequencingRunKey(fileName: string) {
  const stem = fileStem(fileName);
  const targetMarker =
    /[_-]ot(?:[-_]?3)?[-_]?(?:nf|nr|f|r|forward|reverse)(?=[_-]|$)/i;
  const markerMatch = targetMarker.exec(stem);
  if (markerMatch?.index === undefined) return "";

  return stem
    .slice(markerMatch.index + markerMatch[0].length)
    .replace(/^[_-]+/, "")
    .replace(/[_-][A-P]\d{1,3}(?:\.\d+)?(?:_(?:\d+|nf|nr|f|r|forward|reverse))?$/i, "")
    .replace(/[_-]+$/, "");
}

/**
 * Group paired AB1 reads by sample. If the same sample was sequenced in
 * multiple instrument runs, keep each run as a separate numbered sample.
 */
export function groupBatchFiles<T extends { name: string }>(files: readonly T[]) {
  const samples = new Map<string, T[]>();
  for (const file of files) {
    const id = autoSampleId(file.name) || fileStem(file.name) || "sample";
    samples.set(id, [...(samples.get(id) || []), file]);
  }

  const groups: Array<[string, T[]]> = [];
  for (const [sampleId, sampleFiles] of samples) {
    if (sampleFiles.length <= 2) {
      groups.push([sampleId, sampleFiles]);
      continue;
    }

    const runs = new Map<string, T[]>();
    let allFilesHaveRunKeys = true;
    for (const file of sampleFiles) {
      const runKey = sequencingRunKey(file.name);
      if (!runKey) {
        allFilesHaveRunKeys = false;
        break;
      }
      runs.set(runKey, [...(runs.get(runKey) || []), file]);
    }

    const canSplitRuns =
      allFilesHaveRunKeys &&
      runs.size > 1 &&
      Array.from(runs.values()).every((runFiles) => runFiles.length <= 2);
    if (!canSplitRuns) {
      groups.push([sampleId, sampleFiles]);
      continue;
    }

    let replicate = 1;
    for (const runFiles of runs.values()) {
      groups.push([`${sampleId}-${replicate}`, runFiles]);
      replicate += 1;
    }
  }

  return groups;
}
