// Small synthetic fixtures only; none of the real ERB numbers.
export const cellSrc = { source_table: "Table 13", source_pdf_page: 40, source_printed_page: 33 };
export const station = (group: string, name: string, state: "reported" | "not_reported", value: string | null, year = 2030) => ({
  station_group: group, station_name: name, observation_year: year, value_state: state,
  generation_gwh: value, published_precision: value === null ? null : (value.split(".")[1] ?? "").length, ...cellSrc,
});
export const total = (group: string, value: string, year = 2030) => ({
  station_group: group, observation_year: year, total_gwh: value, published_precision: 2, ...cellSrc,
});
export const snapshot = (over: Record<string, unknown> = {}) => ({
  publisher: "ERB", source_series: "annual_statistical_bulletin", publication_year: 2031, revision_sequence: 1,
  title: "Synthetic Bulletin", cover_date: "March 2031", source_url: "https://example.test/bulletin.pdf", rights_status: "review_required",
  observation_year: 2030,
  stations: [
    station("zesco_large_hydro", "Alpha", "reported", "393.40"),
    station("zesco_large_hydro", "Beta", "reported", "0.00"),
    station("zesco_mini_hydro", "Gamma", "not_reported", null),
    station("independent_power_producer", "Delta & Co", "reported", "1.5"),
  ],
  group_totals: [total("zesco_large_hydro", "400.10"), total("zesco_mini_hydro", "2.00"), total("zesco_diesel", "0.00"), total("independent_power_producer", "1.50")],
  ...over,
});
export const hpoint = (year: number, value: string | null) => ({
  observation_year: year, value_state: value === null ? "not_reported" : "reported", generation_gwh: value,
  published_precision: value === null ? null : 2, publication_year: 2031, revision_sequence: 1, ...cellSrc,
});
export const history = (group: string, name: string, points = [hpoint(2029, "10.10"), hpoint(2030, null), hpoint(2031, "12.00")]) => ({
  station_group: group, station_name: name, count: points.length, rights_status: "review_required", observations: points,
});
export const gpoint = (group: string, year: number, v: string) => ({
  station_group: group, observation_year: year, total_gwh: v, published_precision: 2, publication_year: 2031, revision_sequence: 1, ...cellSrc,
});
export const ghistory = (group: string, points = [gpoint(group, 2029, "5.50"), gpoint(group, 2030, "6.60")]) => ({
  station_group: group, count: points.length, rights_status: "review_required", observations: points,
});
