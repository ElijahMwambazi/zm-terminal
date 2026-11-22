import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { snapshot } from "./bankFxFixtures";
import { useBankFx } from "./useBankFx";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const deferred = () => { let resolve!: (r: Response) => void; const promise = new Promise<Response>((r) => { resolve = r; }); return { promise, resolve }; };
const body = (day: string) => new Response(JSON.stringify({ snapshot: snapshot({ observation_date: day }) }));
const yearOf = (s: ReturnType<typeof useBankFx>["state"]) => (s.status === "ready" ? s.data.snapshot?.observation_date : s.status);

describe("useBankFx (stored reads only)", () => {
  it("lets the newest request win when an older one resolves later", async () => {
    const first = deferred(), second = deferred();
    const queue = [first, second];
    vi.stubGlobal("fetch", vi.fn(() => queue.shift()!.promise));
    const { result } = renderHook(() => useBankFx());
    act(() => result.current.refresh());
    await act(async () => { second.resolve(body("2031-01-02")); });
    await waitFor(() => expect(yearOf(result.current.state)).toBe("2031-01-02"));
    await act(async () => { first.resolve(body("2029-01-02")); });
    expect(yearOf(result.current.state)).toBe("2031-01-02");
  });
  it("does not let an older refresh overwrite a later one", async () => {
    const a = deferred(), b = deferred(), c = deferred();
    const queue = [a, b, c];
    vi.stubGlobal("fetch", vi.fn(() => queue.shift()!.promise));
    const { result } = renderHook(() => useBankFx());
    await act(async () => { a.resolve(body("2030-01-02")); });
    act(() => result.current.refresh());
    act(() => result.current.refresh());
    await act(async () => { c.resolve(body("2033-01-02")); });
    await act(async () => { b.resolve(body("2032-01-02")); });
    expect(yearOf(result.current.state)).toBe("2033-01-02");
  });
  it("keeps previous data visible while refreshing", async () => {
    const a = deferred(), b = deferred();
    const queue = [a, b];
    vi.stubGlobal("fetch", vi.fn(() => queue.shift()!.promise));
    const { result } = renderHook(() => useBankFx());
    await act(async () => { a.resolve(body("2030-01-02")); });
    act(() => result.current.refresh());
    expect(result.current.state).toMatchObject({ status: "ready", refreshing: true });
    expect(yearOf(result.current.state)).toBe("2030-01-02");
  });
  it("never updates after unmount", async () => {
    const a = deferred();
    vi.stubGlobal("fetch", vi.fn(() => a.promise));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { result, unmount } = renderHook(() => useBankFx());
    unmount();
    await act(async () => { a.resolve(body("2030-01-02")); });
    expect(result.current.state).toEqual({ status: "loading" });
    expect(errors).not.toHaveBeenCalled();
  });
});
