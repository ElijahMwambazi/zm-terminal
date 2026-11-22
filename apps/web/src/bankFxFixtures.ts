/** SYNTHETIC Bank FX API payloads for the frontend tests: invented institutions and invented rates, never copied from BoZ or the database. */
import type { BankFxSlot } from "./api";

type Row = { source_row_index: number; institution_name: string; institution_name_raw: string; interbank_buy: string; interbank_sell: string; retail_buy: string; retail_sell: string };
export const ALL_SLOTS: BankFxSlot[] = ["09:30", "12:30", "15:30"];

export function row(index: number, name: string, base: number, over: Partial<Row> = {}): Row {
  const f = (n: number) => `${base}.${String(n).padStart(2, "0")}`;
  return { source_row_index: index, institution_name: name, institution_name_raw: `${name}  `, interbank_buy: f(11), interbank_sell: f(22), retail_buy: f(33), retail_sell: f(44), ...over };
}

/** Source order deliberately differs from alphabetical order (ZULU is first, ALPHA is last). */
export function slotRows(base: number, average: boolean | Partial<Row> = true) {
  const banks = [row(3, "ZULU TEST BANK", base), row(4, "BETA FINANCE", base + 1), row(5, "ALPHA BANK", base + 2)];
  const market_average = average === false ? null : row(6, "MARKET AVERAGE", base + 3, typeof average === "object" ? average : {});
  return { banks, market_average };
}

export function slot(label: BankFxSlot, base: number, average: boolean | Partial<Row> = true, layout: "v1" | "v2" = "v2") {
  return { slot: label, retail_source_label: layout === "v1" ? "Retail Rates" : "BOARD RATES", ...slotRows(base, average) };
}

export function snapshot(over: Record<string, unknown> = {}, slots: BankFxSlot[] = ALL_SLOTS, layout: "v1" | "v2" = "v2") {
  return {
    observation_date: "2030-01-02", source_record_id: "00000000-0000-4000-8000-0000000000a1", source_revision_id: 22, source_internal_nid: 11,
    source_created: "2030-01-02T13:36:38+00:00", source_changed: "2030-01-02T13:36:46+00:00", source_revision_timestamp: "2030-01-02T13:36:46+00:00",
    source_title: "SYNTHETIC RATES 02 01 30", source_rates_datetime_raw: "2030-01-02T15:36:15+02:00", source_rates_datetime: "2030-01-02T13:36:15+00:00",
    table_layout: layout, retrieved_at: "2030-01-03T08:00:00+00:00", source_url: "https://example.test/commercial-bank-rates", rights_status: "review_required",
    slots: slots.map((s, i) => slot(s, 10 + i * 10, true, layout)), ...over,
  };
}
