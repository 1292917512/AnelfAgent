import type { ActivityRun, ActivitySkill } from "@/lib/types/activity";

interface SkillParticipation {
  skill: ActivitySkill;
  count: number;
  modes: Record<ActivitySkill["mode"], number>;
  latest: ActivityRun;
}

/** 汇总内存中每轮的技能参与事实，次数相同时优先展示最近活动。 */
export function skillParticipation(runs: ActivityRun[]): SkillParticipation[] {
  const rows = new Map<string, SkillParticipation>();
  for (const run of runs) {
    for (const skill of run.skills ?? []) {
      let row = rows.get(skill.name);
      if (!row) {
        row = { skill, count: 0, modes: { read: 0, candidate: 0 }, latest: run };
        rows.set(skill.name, row);
      }
      row.count += 1;
      row.modes[skill.mode] += 1;
      if (run.started_at >= row.latest.started_at) { row.latest = run; row.skill = skill; }
    }
  }
  return [...rows.values()].sort((a, b) => b.count - a.count || b.latest.started_at - a.latest.started_at || a.skill.name.localeCompare(b.skill.name));
}
