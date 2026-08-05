export const ARTICLE_MODE = "v1_strict_article" as const;
export const PROJECT_ID = "orientia-tsa56-article-typing-v1" as const;
export const PROJECT_VERSION = "1.0.0" as const;

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type TreeTypingResult = {
  sample_id: string;
  predicted_new_genotype?: string;
  predicted_new_group?: string;
  confidence?: string;
  status?: string;
  support?: number | null;
  nearest_reference?: string | null;
  nearest_distance?: number | null;
  auto_accepted?: boolean | null;
  decision_reason?: string | null;
};

export type AnalysisSummary = {
  predicted_type?: string;
  predicted_group?: string;
  confidence?: string;
  target_status?: string;
  typing_source?: string;
  tree_typing_support?: number | null;
  tree_typing_status?: string | null;
  tree_typing_nearest_reference?: string | null;
  tree_typing_nearest_distance?: number | null;
  tree_typing_auto_accepted?: boolean | null;
  tree_typing_decision_reason?: string | null;
  tree_typing_results?: TreeTypingResult[];
  consensus_length?: number;
  warnings?: string[];
};

export type AnalysisJob = {
  job_id: string;
  status: JobStatus;
  mode: string;
  analysis_mode?: string;
  sample_id: string;
  summary: AnalysisSummary | null;
  files: Record<string, string>;
  error: string | null;
  progress?: {
    stage?: string;
    message?: string;
    percent?: number;
    elapsed_seconds?: number;
  };
};

const configuredBaseUrl = (
  import.meta as ImportMeta & { env?: Record<string, string | undefined> }
).env?.VITE_PHYLO_PUBLIC_API_BASE_URL;

export const API_BASE_URL = (configuredBaseUrl || "http://127.0.0.1:8200").replace(/\/+$/, "");

export class AnalysisApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AnalysisApiError";
    this.status = status;
  }
}

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body
      ? String((body as { detail?: unknown }).detail)
      : `Analysis service returned HTTP ${response.status}`;
    throw new AnalysisApiError(detail, response.status);
  }
  return body as T;
}

export async function getServiceHealth() {
  const response = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
  const body = await readJson<{
    project_id?: string;
    project_version?: string;
    status?: string;
    mafft_available?: boolean;
    iqtree_available?: boolean;
  }>(response);
  return {
    ...body,
    compatible: body.project_id === PROJECT_ID && body.project_version === PROJECT_VERSION,
    ready: body.status === "ok" && body.mafft_available === true && body.iqtree_available === true,
  };
}

export async function submitAb1(input: {
  sampleId: string;
  files: File[];
  mode?: typeof ARTICLE_MODE | "prepare";
  submissionId?: string;
  submissionSampleCount?: number;
}) {
  const form = new FormData();
  form.set("sample_id", input.sampleId.trim() || "sample");
  form.set("mode", input.mode || ARTICLE_MODE);
  form.set("quality_threshold", "35");
  form.set("min_overlap", "80");
  form.set("tree_reference", "60");
  if (input.submissionId) form.set("submission_id", input.submissionId);
  if (input.submissionSampleCount) {
    form.set("submission_sample_count", String(input.submissionSampleCount));
  }
  if (input.files[0]) form.set("read1_file", input.files[0]);
  if (input.files[1]) form.set("read2_file", input.files[1]);
  return readJson<AnalysisJob>(await fetch(`${API_BASE_URL}/api/analyze`, {
    method: "POST",
    body: form,
  }));
}

export async function submitBatchTree(jobIds: string[], submissionId: string) {
  const form = new FormData();
  form.set("job_ids", JSON.stringify(jobIds));
  form.set("tree_method", ARTICLE_MODE);
  form.set("tree_reference", "60");
  form.set("submission_id", submissionId);
  return readJson<AnalysisJob>(await fetch(`${API_BASE_URL}/api/batch-tree`, {
    method: "POST",
    body: form,
  }));
}

export async function submitFasta(file: File) {
  const form = new FormData();
  form.set("fasta_file", file);
  form.set("analysis_mode", ARTICLE_MODE);
  form.set("tree_reference", "60");
  return readJson<AnalysisJob>(await fetch(`${API_BASE_URL}/api/fasta-analysis`, {
    method: "POST",
    body: form,
  }));
}

export async function getJob(jobId: string) {
  return readJson<AnalysisJob>(await fetch(
    `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}`,
    { cache: "no-store" },
  ));
}

export async function cancelJob(jobId: string) {
  return readJson<AnalysisJob>(await fetch(
    `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
  ));
}

export async function waitForJob(
  initial: AnalysisJob,
  onProgress: (job: AnalysisJob) => void,
) {
  let job = initial;
  onProgress(job);
  while (job.status === "queued" || job.status === "running") {
    await new Promise((resolve) => setTimeout(resolve, 1800));
    job = await getJob(job.job_id);
    onProgress(job);
  }
  if (job.status === "failed") throw new AnalysisApiError(job.error || "Analysis failed", 500);
  if (job.status === "cancelled") throw new AnalysisApiError("Analysis stopped", 499);
  return job;
}

export function resultFileUrl(jobId: string, filename: string) {
  if (/^https?:\/\//i.test(filename)) return filename;
  if (filename.startsWith("/")) return `${API_BASE_URL}${filename}`;
  return `${API_BASE_URL}/api/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(filename)}`;
}
