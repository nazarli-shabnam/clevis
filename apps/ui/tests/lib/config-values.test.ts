import { describe, expect, it } from "vitest";

import { initialConfigValues, mergeSavedConfigValue } from "@/lib/config-values";

describe("initialConfigValues", () => {
  it("seeds the form from the server on first load", () => {
    const server = { worker_poll_seconds: "30", registration_enabled: "true" };
    expect(initialConfigValues({}, server)).toEqual(server);
  });

  it("preserves unsaved edits when config refetches", () => {
    const current = {
      worker_poll_seconds: "45",
      registration_enabled: "false",
    };
    const server = {
      worker_poll_seconds: "30",
      registration_enabled: "true",
    };
    expect(initialConfigValues(current, server)).toEqual(current);
  });
});

describe("mergeSavedConfigValue", () => {
  it("updates only the saved key after a successful save", () => {
    const current = {
      worker_poll_seconds: "45",
      registration_enabled: "false",
    };
    const server = {
      worker_poll_seconds: "45",
      registration_enabled: "true",
    };
    expect(mergeSavedConfigValue(current, server, "worker_poll_seconds")).toEqual({
      worker_poll_seconds: "45",
      registration_enabled: "false",
    });
  });
});
