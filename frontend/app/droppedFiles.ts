export type DroppedFileSystemEntry = {
  isFile: boolean;
  isDirectory: boolean;
  file?: (
    success: (file: File) => void,
    error?: (reason: unknown) => void,
  ) => void;
  createReader?: () => {
    readEntries: (
      success: (entries: DroppedFileSystemEntry[]) => void,
      error?: (reason: unknown) => void,
    ) => void;
  };
};

type DataTransferItemWithEntry = {
  webkitGetAsEntry?: () => DroppedFileSystemEntry | null;
};

function fileFromEntry(entry: DroppedFileSystemEntry) {
  return new Promise<File>((resolve, reject) => {
    if (!entry.file) {
      reject(new Error("拖入项目不是可读取的文件"));
      return;
    }
    entry.file(resolve, reject);
  });
}

async function directoryEntries(entry: DroppedFileSystemEntry) {
  if (!entry.createReader) {
    throw new Error("浏览器无法读取拖入的文件夹");
  }

  const reader = entry.createReader();
  const entries: DroppedFileSystemEntry[] = [];

  // Chrome may return directory contents in several batches, so keep
  // reading until the API returns an empty batch.
  for (;;) {
    const batch = await new Promise<DroppedFileSystemEntry[]>((resolve, reject) => {
      reader.readEntries(resolve, reject);
    });
    if (batch.length === 0) return entries;
    entries.push(...batch);
  }
}

export async function collectDroppedEntryFiles(
  entry: DroppedFileSystemEntry,
): Promise<File[]> {
  if (entry.isFile) return [await fileFromEntry(entry)];
  if (!entry.isDirectory) return [];

  const children = await directoryEntries(entry);
  const nestedFiles = await Promise.all(children.map(collectDroppedEntryFiles));
  return nestedFiles.flat();
}

export async function collectDroppedFiles(dataTransfer: DataTransfer) {
  const entries = Array.from(dataTransfer.items)
    .map((item) =>
      (item as unknown as DataTransferItemWithEntry).webkitGetAsEntry?.() ?? null
    )
    .filter((entry): entry is DroppedFileSystemEntry => entry !== null);

  if (entries.length === 0) {
    return Array.from(dataTransfer.files);
  }

  const files = await Promise.all(entries.map(collectDroppedEntryFiles));
  return files.flat();
}
