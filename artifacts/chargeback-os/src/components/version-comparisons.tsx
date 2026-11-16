import { Link } from 'wouter';
import type { Run } from '@/lib/sandbox-api';
import { buildComparisonSummaries, noFailedChecksStats, type ComparisonPair } from '@/lib/comparison-summary';

function Variability({ samples, arm }: { samples: ComparisonPair[]; arm: 0 | 1 }) {
  const stats = noFailedChecksStats(samples, arm);
  const outcomes = new Map<string, number>();
  for (const pair of samples) {
    const run = pair.members.find(member => member.comparison?.arm === arm);
    if (run) outcomes.set(run.observations?.outcome ?? 'unavailable', (outcomes.get(run.observations?.outcome ?? 'unavailable') ?? 0) + 1);
  }
  return <div>
    <p><strong>{stats.count} completed paired sample{stats.count === 1 ? '' : 's'}</strong></p>
    <p>Runs with no recorded failed checks: {stats.mean === null ? '—' : `${Math.round(stats.mean * 100)}%`}</p>
    <p>Variability: {stats.deviation === null ? 'not enough samples to estimate' : `sample SD ${Math.round(stats.deviation * 100)} percentage points`}</p>
    <p>Observed outcomes: {[...outcomes].map(([outcome, count]) => `${outcome.replaceAll('_', ' ')} ${count}/${stats.count}`).join('; ') || 'none yet'}</p>
  </div>;
}

function RunDetails({ run }: { run: Run }) {
  return <div className="sb-panel" key={run.id}>
    <h4>Trial {run.comparison?.trial ?? 1} · {run.agent_label}</h4>
    <p className="sb-sub">{run.state.status} · {run.consent ? 'Research opt-in' : 'Private'} · scorer {run.score.scorer_version}</p>
    <dl>
      <dt>Observable outcome</dt><dd>{run.observations?.outcome.replaceAll('_', ' ') ?? 'Unavailable'}</dd>
      <dt>Reached action / final claim</dt><dd>{run.observations?.reached_action ? 'Yes' : 'No'} / {run.observations?.reached_final_claim ? 'Yes' : 'No'}</dd>
      <dt>Logical actions / replayed retries</dt><dd>{run.observations?.logical_actions ?? '—'} / {run.observations?.transport_retries ?? '—'}</dd>
      <dt>First action after creation</dt><dd>{run.observations?.first_action_seconds == null ? 'Not observed' : `${run.observations.first_action_seconds.toFixed(1)} seconds`}</dd>
      <dt>Synthetic ticks</dt><dd>{run.observations?.synthetic_ticks ?? '—'}</dd>
    </dl>
    <Link className="sb-btn" href={`/runs/${run.id}`}>Open {run.agent_label}</Link>
  </div>;
}

/** Summaries are computed only from the owner's currently retained runs. */
export function VersionComparisons({ runs }: { runs: Run[] }) {
  const comparisons = buildComparisonSummaries(runs);
  if (!comparisons.length) return null;
  return <section className="sb-section" data-testid="version-comparisons">
    <h2>Version comparisons</h2>
    <p className="sb-sub">Labels are self-reported. These are operator-selected scenarios, not blind benchmarks or model rankings. Incomplete, active, and incompatible trials are excluded from sample summaries. No-failure counts are scripted observations, not safety certification. Each run keeps independent credentials, consent, and retention.</p>
    {comparisons.map(comparison => {
      const incomplete = comparison.pairs.filter(pair => !pair.complete || !pair.compatible).length;
      return <div className="sb-panel" key={comparison.id} style={{ marginTop: 16 }}>
        <h3>{comparison.scenario.replaceAll('_', ' ')} · {comparison.simulatorVersion || 'version unavailable'}</h3>
        <p className="sb-sub">Scorer {comparison.scorerVersion || 'version unavailable'} · {comparison.samples.length} complete paired sample{comparison.samples.length === 1 ? '' : 's'} of {comparison.trialsPlanned} planned · {incomplete} incomplete, active, or incompatible trial{incomplete === 1 ? '' : 's'}</p>
        {comparison.labels && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 20, marginTop: 16 }}>
          {comparison.labels.map((label, arm) => <div key={`${arm}-${label}`}>
            <h4>{label}</h4>
            <Variability samples={comparison.samples} arm={arm as 0 | 1}/>
          </div>)}
        </div>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14, marginTop: 18 }}>
          {comparison.pairs.flatMap(pair => {
            const note = !pair.compatible
              ? 'Incomplete or incompatible pair — a member may have been deleted or reached its retention deadline. It is not a paired sample.'
              : !pair.complete
                ? 'In progress or missing a final claim. This trial is not included in the sample summary.'
                : `Completed paired sample ${pair.trial} of ${comparison.trialsPlanned}.`;
            return [
              <div key={`${pair.id}-status`} className="sb-notice" style={{ gridColumn: '1 / -1' }}>{note}</div>,
              ...pair.members.map(run => <RunDetails key={run.id} run={run}/>),
            ];
          })}
        </div>
        <p className="sb-sub" style={{ marginTop: 16 }}>First-action time includes operator setup. Retries count accepted idempotency replays only, not rejected HTTP requests. Research consent and deletion apply separately to each run; removing a run also removes it from these derived summaries.</p>
      </div>;
    })}
  </section>;
}
