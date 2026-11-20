import { describe, expect, it } from "vitest";
import { select } from "./selection";

const order = ["a", "b", "c", "d", "e"];

describe("grid selection", () => {
  it("click picks one, ⌘-click toggles, ⇧-click takes the range", () => {
    let s = select({ ids: [], anchor: null }, "b", order);
    expect(s).toEqual({ ids: ["b"], anchor: "b" });
    s = select(s, "d", order, { toggle: true });
    expect(s.ids).toEqual(["b", "d"]);
    s = select(s, "b", order, { toggle: true });
    expect(s).toEqual({ ids: ["d"], anchor: "b" });
    s = select({ ids: ["b"], anchor: "b" }, "e", order, { range: true });
    expect(s.ids).toEqual(["b", "c", "d", "e"]);
    expect(select(s, "a", order, { range: true }).ids).toEqual(["a", "b"]);
  });
  it("keeps the shown order whatever the click order", () => {
    let s = select({ ids: [], anchor: null }, "d", order);
    s = select(s, "a", order, { toggle: true });
    expect(s.ids).toEqual(["a", "d"]);
  });
});
