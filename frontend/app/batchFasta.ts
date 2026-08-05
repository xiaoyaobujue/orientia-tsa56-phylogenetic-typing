export type BatchConsensusRecord = {
  sampleId: string;
  fastaText: string;
};

export function combineBatchConsensusFasta(records: BatchConsensusRecord[]) {
  return records.map(({ sampleId, fastaText }) => {
    const sequence = fastaText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith(">"))
      .join("")
      .replace(/\s+/g, "")
      .toUpperCase();
    if (!sequence) {
      throw new Error(`${sampleId} 的质控拼接序列为空`);
    }

    const safeSampleId = sampleId.trim().replace(/[\s>]+/g, "_") || "sample";
    const wrapped = sequence.match(/.{1,80}/g)?.join("\n") || sequence;
    return `>${safeSampleId}\n${wrapped}`;
  }).join("\n") + "\n";
}
