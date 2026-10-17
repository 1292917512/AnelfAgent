import { expect, it } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { skillParticipation } from "./skill-activity";

it("ranks recent participation by count, keeps latest context, and separates evidence modes", () => {
  const base: ActivityRun = { id: "run-1", scope: "", origin_scope: "", actor: "", label: "", kind: "task", input: "", source: { scope: "", kind: "", channel: "", target: "", session: "" }, owner_id: "", parent_id: "", status: "completed", started_at: 1, updated_at: 1, ended_at: 1, revision: 1, entries: [], entry_count: 0, truncated: false };
  const runs: ActivityRun[] = [
    { ...base, skills: [{ name: "frequent", description: "old", mode: "candidate" }] },
    { ...base, id: "run-2", started_at: 2, skills: [{ name: "frequent", description: "current", mode: "read" }, { name: "earlier", description: "", mode: "candidate" }] },
    { ...base, id: "run-3", started_at: 3, skills: [{ name: "latest", description: "", mode: "read" }] },
  ];
  const rows = skillParticipation(runs);
  expect(rows.map((row) => row.skill.name)).toEqual(["frequent", "latest", "earlier"]);
  expect(rows[0]).toMatchObject({ count: 2, modes: { read: 1, candidate: 1 }, skill: { description: "current" }, latest: { id: "run-2" } });
  expect(skillParticipation([])).toEqual([]);
});
