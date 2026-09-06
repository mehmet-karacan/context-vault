import { describe, expect, it } from "vitest";
import { ApiProblem } from "../../lib/api/client";
import {
  documentReducer,
  emptyDocumentState,
  type DocumentItem,
} from "./documentState";
const item = { id: "source-one" } as DocumentItem;
const started = documentReducer(emptyDocumentState("one"), {
  type: "start",
  scope: "one",
  request: 1,
});
const ready = documentReducer(started, {
  type: "loaded",
  scope: "one",
  request: 1,
  documents: [item],
});
describe("scoped document snapshot lifecycle", () => {
  it("distinguishes unselected, first loading and confirmed empty", () => {
    expect(emptyDocumentState("").documents).toEqual([]);
    expect(started.documents).toBeNull();
    expect(started.updating).toBe(true);
    expect(
      documentReducer(started, {
        type: "loaded",
        scope: "one",
        request: 1,
        documents: [],
      }).documents,
    ).toEqual([]);
  });
  it("retains only an existing validated snapshot on an outage", () => {
    const cause = new ApiProblem(503, "outage", "PRIVATE detail");
    const partial = documentReducer(ready, {
      type: "failed",
      scope: "one",
      request: 1,
      cause,
    });
    expect(partial.documents).toEqual([item]);
    expect(partial.error).not.toContain("PRIVATE");
    expect(
      documentReducer(started, {
        type: "failed",
        scope: "one",
        request: 1,
        cause,
      }).documents,
    ).toBeNull();
  });
  it.each([401, 403, 404])("invalidates cached rows on HTTP %s", (status) => {
    const result = documentReducer(ready, {
      type: "failed",
      scope: "one",
      request: 1,
      cause: new ApiProblem(status, "denied", "private"),
    });
    expect(result.documents).toBeNull();
    expect(result.denied).toBe(true);
  });
  it("does not accept a delayed response after a newer request", () => {
    const newer = documentReducer(ready, {
      type: "start",
      scope: "one",
      request: 2,
    });
    expect(
      documentReducer(newer, {
        type: "loaded",
        scope: "one",
        request: 1,
        documents: [],
      }),
    ).toBe(newer);
    expect(
      documentReducer(newer, {
        type: "failed",
        scope: "one",
        request: 1,
        cause: Error("old"),
      }),
    ).toBe(newer);
  });
  it("never carries a snapshot or delayed result across scope", () => {
    const other = documentReducer(ready, {
      type: "start",
      scope: "two",
      request: 2,
    });
    expect(other.documents).toBeNull();
    expect(
      documentReducer(other, {
        type: "loaded",
        scope: "one",
        request: 1,
        documents: [item],
      }),
    ).toBe(other);
  });
  it("does not resurrect a successfully deleted row when refresh fails", () => {
    const removed = documentReducer(ready, {
      type: "removed",
      scope: "one",
      request: 1,
      id: item.id,
    });
    expect(
      documentReducer(removed, {
        type: "failed",
        scope: "one",
        request: 1,
        cause: Error("network"),
      }).documents,
    ).toEqual([]);
  });
  it("retains denial until an explicit successful revalidation", () => {
    const denied = documentReducer(ready, {
      type: "failed",
      scope: "one",
      request: 1,
      cause: new ApiProblem(403, "denied", "denied"),
    });
    const retry = documentReducer(denied, {
      type: "start",
      scope: "one",
      request: 2,
    });
    expect(
      documentReducer(retry, {
        type: "failed",
        scope: "one",
        request: 2,
        cause: Error("network"),
      }).denied,
    ).toBe(true);
    const recovered = documentReducer(retry, {
      type: "loaded",
      scope: "one",
      request: 2,
      documents: [item],
    });
    expect(recovered.denied).toBe(false);
    expect(recovered.error).toBeNull();
  });
});
