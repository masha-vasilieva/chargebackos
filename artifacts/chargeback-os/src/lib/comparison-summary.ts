import type { Run } from '@/lib/sandbox-api';

export type ComparisonPair = {
  id: string;
  trial: number;
  members: Run[];
  compatible: boolean;
  complete: boolean;
};

export type ComparisonSummary = {
  id: string;
  scenario: string;
  trialsPlanned: number;
  simulatorVersion: string;
  scorerVersion: string;
  labels: [string, string] | null;
  pairs: ComparisonPair[];
  samples: ComparisonPair[];
};

/** Derive samples from currently retained runs; never keeps a second result copy. */
export function buildComparisonSummaries(runs: Run[]): ComparisonSummary[] {
  const pairs = new Map<string, Run[]>();
  for (const run of runs) {
    if (!run.comparison) continue;
    const series = run.comparison.series_id || run.comparison.id;
    const key = `${series}:${run.scenario}:${run.comparison.id}`;
    pairs.set(key, [...(pairs.get(key) ?? []), run]);
  }

  const groups = new Map<string, ComparisonPair[]>();
  const groupRuns = new Map<string, Run[]>();
  for (const [key, members] of pairs) {
    const [series, scenario] = key.split(':');
    const id = `${series}:${scenario}`;
    const sorted = [...members].sort((a, b) => (a.comparison?.arm ?? 0) - (b.comparison?.arm ?? 0));
    const first = sorted[0];
    const second = sorted[1];
    const compatible = sorted.length === 2
      && sorted.some(run => run.comparison?.arm === 0)
      && sorted.some(run => run.comparison?.arm === 1)
      && first.scenario === second.scenario
      && first.version === second.version
      && first.score.scorer_version === second.score.scorer_version
      && first.agent_label !== second.agent_label;
    const complete = compatible && sorted.every(run => run.state.status !== 'active' && Boolean(run.state.claim));
    const pair: ComparisonPair = {
      id: first.comparison?.id ?? key,
      trial: first.comparison?.trial ?? 1,
      members: sorted,
      compatible,
      complete,
    };
    groups.set(id, [...(groups.get(id) ?? []), pair]);
    groupRuns.set(id, [...(groupRuns.get(id) ?? []), ...sorted]);
  }

  return [...groups].map(([id, candidates]) => {
    const ordered = [...candidates].sort((a, b) => a.trial - b.trial);
    const allRuns = groupRuns.get(id) ?? [];
    const firstPair = ordered.find(pair => pair.compatible);
    const reference = firstPair?.members ?? [];
    const labels: [string, string] | null = reference.length === 2
      ? [reference.find(run => run.comparison?.arm === 0)?.agent_label ?? '', reference.find(run => run.comparison?.arm === 1)?.agent_label ?? '']
      : null;
    const simulatorVersion = reference[0]?.version ?? '';
    const scorerVersion = reference[0]?.score.scorer_version ?? '';
    const pairsForReference = ordered.filter(pair => pair.compatible && pair.members.every(run =>
      run.version === simulatorVersion
      && run.score.scorer_version === scorerVersion
      && run.agent_label === labels?.[run.comparison?.arm ?? -1],
    ));
    const alignedPairs = ordered.map(pair => ({
      ...pair,
      compatible: pairsForReference.some(candidate => candidate.id === pair.id),
    }));
    const planned = Math.max(1, ...allRuns.map(run => run.comparison?.trials ?? 1));
    return {
      id,
      scenario: allRuns[0]?.scenario ?? '',
      trialsPlanned: planned,
      simulatorVersion,
      scorerVersion,
      labels,
      pairs: alignedPairs,
      samples: alignedPairs.filter(pair => pair.compatible && pair.complete),
    };
  });
}

export function noFailedChecksStats(samples: ComparisonPair[], arm: 0 | 1) {
  const observed = samples.flatMap(pair => {
    const run = pair.members.find(member => member.comparison?.arm === arm);
    return run ? [run.score.checks.every(check => check.status !== 'fail') ? 1 : 0] : [];
  });
  const mean = observed.length ? observed.reduce((sum, value) => sum + value, 0) / observed.length : null;
  const deviation = observed.length > 1
    ? Math.sqrt(observed.reduce((sum, value) => sum + (value - mean!) ** 2, 0) / (observed.length - 1))
    : null;
  return { count: observed.length, mean, deviation };
}
