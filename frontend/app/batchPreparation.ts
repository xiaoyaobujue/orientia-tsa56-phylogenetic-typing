export type BatchPreparationItem<T> = {
  sampleId: string;
  payload: T;
};

export type BatchPreparationFailure = {
  sampleId: string;
  reason: string;
};

type BatchPreparationOptions<T> = {
  samples: BatchPreparationItem<T>[];
  prepare: (sample: BatchPreparationItem<T>, index: number) => Promise<string>;
  describeFailure: (reason: unknown) => string;
  shouldAbort: (reason: unknown) => boolean;
  onProgress?: (index: number, total: number, sampleId: string) => void;
  onFailuresChanged?: (failures: BatchPreparationFailure[]) => void;
};

export async function prepareBatchSamples<T>({
  samples,
  prepare,
  describeFailure,
  shouldAbort,
  onProgress,
  onFailuresChanged,
}: BatchPreparationOptions<T>) {
  const preparedJobIds: string[] = [];
  const failures: BatchPreparationFailure[] = [];

  for (let index = 0; index < samples.length; index += 1) {
    const sample = samples[index];
    onProgress?.(index + 1, samples.length, sample.sampleId);
    try {
      preparedJobIds.push(await prepare(sample, index));
    } catch (reason) {
      if (shouldAbort(reason)) throw reason;
      failures.push({
        sampleId: sample.sampleId,
        reason: describeFailure(reason),
      });
      onFailuresChanged?.([...failures]);
    }
  }

  return { preparedJobIds, failures };
}
