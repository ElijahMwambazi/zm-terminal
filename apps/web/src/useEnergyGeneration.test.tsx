import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { snapshot } from "./energyFixtures";
import { useEnergyGeneration } from "./useEnergyGeneration";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const deferred = () => { let resolve!: (r: Response) => void; const promise = new Promise<Response>((r) => { resolve = r; }); return { promise, resolve }; };
const body = (year: number) => new Response(JSON.stringify({ snapshot: snapshot({ observation_year: year, stations: [], group_totals: [] }) }));
const yearOf = (s: ReturnType<typeof useEnergyGeneration>["state"]) => (s.status === "ready" ? s.data.snapshot?.observation_year : s.status);

describe("useEnergyGeneration", () => {
  it("lets the newest request win when an older one resolves later", async () => {
    const first = deferred(), second = deferred();
    const queue = [first, second];
    vi.stubGlobal("fetch", vi.fn(() => queue.shift()!.promise));
    const { result } = renderHook(() => useEnergyGeneration());
    act(() => result.current.refresh());
    await act(async () => { second.resolve(body(2031)); });
    await waitFor(() => expect(yearOf(result.current.state)).toBe(2031));
    await act(async () => { first.resolve(body(2029)); });
    expect(yearOf(result.current.state)).toBe(2031);
  });
  it("does not let an older refresh overwrite a later one", async () => {
    const a = deferred(), b = deferred(), c = deferred();
    const queue = [a, b, c];
    vi.stubGlobal("fetch", vi.fn(() => queue.shift()!.promise));
    const { result } = renderHook(() => useEnergyGeneration());
    await act(async () => { a.resolve(body(2030)); });
    act(() => result.current.refresh());
    act(() => result.current.refresh());
    await act(async () => { c.resolve(body(2033)); });
    await act(async () => { b.resolve(body(2032)); });
    expect(yearOf(result.current.state)).toBe(2033);
  });
  it("keeps previous data visible while refreshing", async () => {
    const a = deferred(), b = deferred();
    const queue = [a, b];
    vi.stubGlobal("fetch", vi.fn(() => queue.shift()!.promise));
    const { result } = renderHook(() => useEnergyGeneration());
    await act(async () => { a.resolve(body(2030)); });
    act(() => result.current.refresh());
    expect(result.current.state).toMatchObject({ status: "ready", refreshing: true });
    expect(yearOf(result.current.state)).toBe(2030);
  });
  it("never updates after unmount", async () => {
    const a = deferred();
    vi.stubGlobal("fetch", vi.fn(() => a.promise));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { result, unmount } = renderHook(() => useEnergyGeneration());
    unmount();
    await act(async () => { a.resolve(body(2030)); });
    expect(result.current.state).toEqual({ status: "loading" });
    expect(errors).not.toHaveBeenCalled();
  });
});
