export type TypingResultCsvRow = {
  sample_id: string;
  predicted_new_genotype?: string;
  predicted_old_genotype?: string;
  predicted_new_group?: string;
  predicted_old_group?: string;
  support?: number | null;
  status?: string;
};

export type TypingResultCsvColumns = {
  sequence: string;
  newGenotype: string;
  oldGenotype: string;
  newGroup: string;
  oldGroup: string;
  support: string;
  status: string;
};

function csvCell(value: unknown) {
  let text = value === null || value === undefined || value === "" ? "—" : String(value);
  if (/^[\t\r\n ]*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function supportLabel(value?: number | null) {
  if (value === null || value === undefined) return "—";
  return value <= 1 ? `${Math.round(value * 100)}%` : `${Math.round(value)}%`;
}

export function buildTypingResultsCsv(
  rows: readonly TypingResultCsvRow[],
  columns: TypingResultCsvColumns,
  decisionLabel: (status?: string) => string,
) {
  const header = [
    columns.sequence,
    columns.newGenotype,
    columns.oldGenotype,
    columns.newGroup,
    columns.oldGroup,
    columns.support,
    columns.status,
  ];
  const records = rows.map((row) => [
    row.sample_id,
    row.predicted_new_genotype,
    row.predicted_old_genotype,
    row.predicted_new_group,
    row.predicted_old_group,
    supportLabel(row.support),
    decisionLabel(row.status),
  ]);

  return `\uFEFF${[header, ...records]
    .map((record) => record.map(csvCell).join(","))
    .join("\r\n")}\r\n`;
}
