import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import { describeJob, forgetJob, listTrackedJobs, trackJob } from "@/lib/running-jobs";

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("running job tracking (#1124)", () => {
  it("describes each long operation by its endpoint", () => {
    expect(describeJob("/me/analytics/overview", { owner: "acme" })).toEqual({ label: "security scan", org: "acme" });
    expect(describeJob("/orgs/acme%20x/dependabot-triage", {})).toEqual({ label: "Dependabot triage", org: "acme x" });
    expect(describeJob("/orgs/acme/branch-protection/bulk", {}).label).toBe("bulk branch-protection change");
  });

  it("remembers a job until it is forgotten and drops entries too old to re-attach to", () => {
    trackJob({ jobId: 1, label: "security scan", org: "acme", startedAt: Date.now() });
    trackJob({ jobId: 2, label: "bulk fix", org: "acme", startedAt: Date.now() - 36 * 60_000 });
    expect(listTrackedJobs().map((j) => j.jobId)).toEqual([1]);
    forgetJob(1);
    expect(listTrackedJobs()).toEqual([]);
  });

  it("drops records whose fields are malformed and keeps the valid ones", () => {
    const ok = { jobId: 1, label: "bulk fix", org: null, startedAt: Date.now() };
    localStorage.setItem("clevis:running-jobs", JSON.stringify([ok, { ...ok, jobId: 2, label: {} }, { ...ok, jobId: 3, startedAt: "x" }]));
    expect(listTrackedJobs().map((j) => j.jobId)).toEqual([1]);
  });

  it("survives corrupt storage", () => {
    localStorage.setItem("clevis:running-jobs", "{not json");
    expect(listTrackedJobs()).toEqual([]);
  });
});

describe("postJob tracks the job it starts", () => {
  function stub(poll: () => Response | Promise<Response>) {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? Promise.resolve(new Response(JSON.stringify({ job_id: 9 }), { status: 202 }))
          : Promise.resolve(poll()),
      ),
    );
  }

  it("keeps a job that may still be running, and forgets one that finished", async () => {
    vi.useFakeTimers();
    stub(() => new Response("{}", { status: 503 }));
    const lost = api.dependabotTriage.run("acme", { dry_run: false });
    const assertion = expect(lost).rejects.toMatchObject({ name: "JobStillRunningError" });
    await vi.advanceTimersByTimeAsync(6 * 2000);
    await assertion;
    expect(listTrackedJobs().map((j) => j.jobId)).toEqual([9]); // still re-attachable

    localStorage.clear();
    stub(() => new Response(JSON.stringify({ id: 9, job_type: "api.x", status: "done", result: "{}" }), { status: 200 }));
    const done = api.dependabotTriage.run("acme", { dry_run: false });
    await vi.advanceTimersByTimeAsync(2100);
    await done;
    expect(listTrackedJobs()).toEqual([]);
  });
});
