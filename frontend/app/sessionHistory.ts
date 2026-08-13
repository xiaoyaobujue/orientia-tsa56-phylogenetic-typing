import type { JobStatus } from "./analysisApi";

export type HistoryInputMode = "single" | "batch" | "fasta";

export type SessionHistoryEntry = {
  jobId: string;
  label: string;
  mode: HistoryInputMode;
  submittedAt: number;
  status: JobStatus;
};

export const SESSION_HISTORY_KEY = "orientia-tsa56-session-history-v1";
export const SESSION_HISTORY_LIMIT = 30;

const jobStatuses = new Set<JobStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
]);
const inputModes = new Set<HistoryInputMode>(["single", "batch", "fasta"]);

function isSessionHistoryEntry(value: unknown): value is SessionHistoryEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<SessionHistoryEntry>;
  return typeof entry.jobId === "string"
    && /^[a-f0-9]{32}$/.test(entry.jobId)
    && typeof entry.label === "string"
    && entry.label.length > 0
    && entry.label.length <= 160
    && typeof entry.submittedAt === "number"
    && Number.isFinite(entry.submittedAt)
    && jobStatuses.has(entry.status as JobStatus)
    && inputModes.has(entry.mode as HistoryInputMode);
}

export function parseSessionHistory(serialized: string | null) {
  if (!serialized) return [];
  try {
    const parsed: unknown = JSON.parse(serialized);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isSessionHistoryEntry).slice(0, SESSION_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

export function upsertSessionHistory(
  entries: SessionHistoryEntry[],
  nextEntry: SessionHistoryEntry,
) {
  return [
    nextEntry,
    ...entries.filter((entry) => entry.jobId !== nextEntry.jobId),
  ].slice(0, SESSION_HISTORY_LIMIT);
}

export function updateSessionHistoryStatus(
  entries: SessionHistoryEntry[],
  jobId: string,
  status: JobStatus,
) {
  return entries.map((entry) => entry.jobId === jobId ? { ...entry, status } : entry);
}
