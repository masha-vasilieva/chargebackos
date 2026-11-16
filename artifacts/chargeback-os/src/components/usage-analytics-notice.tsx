type UsageAnalyticsNoticeProps = {
  className?: string;
};

export function UsageAnalyticsNotice({ className }: UsageAnalyticsNoticeProps) {
  return (
    <p className={className} data-testid="text-usage-analytics-notice">
      We use Replit’s built-in visitor analytics to count page views; it does
      not read your runs, tokens or research records.
      When Replit analytics is enabled, the browser sends only a setup-start
      event with a fixed public URL. The API service counts successful run
      creations, newly accepted agent actions (excluding idempotent replays),
      and completed runs. If the dedicated service-operator credential is
      configured, these three totals are stored in daily UTC buckets for up to
      90 days and are available only to authenticated service operators;
      otherwise they remain in memory and reset when the API process restarts.
      The operational counters contain no run IDs, owner IDs, labels, scenario,
      mode, action details, credentials, or event content; they are not linked
      to private runs, derived from private run traces, or used to infer research
      consent. They are separate from optional research contribution and do not
      change run retention or deletion. Deleting an individual run cannot remove
      or revise an unlinked aggregate. Infrastructure backup copies follow a
      separate backup-retention policy and may remain beyond live-store
      deletion.
      Replit’s automatic page views are separate and may include private run
      paths; those paths do not grant access to run data.
    </p>
  );
}
