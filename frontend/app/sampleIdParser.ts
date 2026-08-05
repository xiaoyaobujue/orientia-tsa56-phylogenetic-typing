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
