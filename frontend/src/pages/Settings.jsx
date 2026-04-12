import { useSchedulerStatus, useRunSpikeCheck } from '../hooks/useAgent'
import { SkeletonBlock } from '../components/ui/Spinner'
import { timeAgo } from '../utils/formatters'

function Section({ title, children }) {
  return (
    <div className="bg-card rounded-2xl border" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <div className="px-5 py-4 border-b" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
        <h2 className="font-display font-semibold text-base text-text-primary">{title}</h2>
      </div>
      <div className="px-5 py-4 space-y-4">{children}</div>
    </div>
  )
}

function Row({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-text-secondary">{label}</span>
      <div>{children}</div>
    </div>
  )
}

export default function Settings() {
  const { data: scheduler, isLoading } = useSchedulerStatus()
  const spikeCheck = useRunSpikeCheck()

  const telegramConfigured = scheduler?.telegram?.configured
  const spikeInfo = scheduler?.spike_detector

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <h1 className="font-display text-2xl font-semibold text-text-primary">Settings</h1>

      {/* System Status */}
      <Section title="System Status">
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => <SkeletonBlock key={i} className="h-4 w-full" />)}
          </div>
        ) : (
          <>
            <Row label="Scheduler">
              <span className={`flex items-center gap-1.5 text-sm font-medium ${scheduler?.is_running ? 'text-success' : 'text-error'}`}>
                <span className={`w-2 h-2 rounded-full ${scheduler?.is_running ? 'bg-success animate-pulse' : 'bg-error'}`} />
                {scheduler?.is_running ? 'Running' : 'Stopped'}
              </span>
            </Row>
            <Row label="Active Jobs">
              <span className="font-mono text-sm">{scheduler?.total_jobs ?? 0}</span>
            </Row>
            <Row label="Telegram">
              <span className={`text-sm font-medium ${telegramConfigured ? 'text-success' : 'text-text-muted'}`}>
                {telegramConfigured ? 'Configured ✓' : 'Not configured'}
              </span>
            </Row>
            <Row label="Last Spike Check">
              <span className="text-sm text-text-secondary font-mono">
                {spikeInfo?.last_check ? timeAgo(spikeInfo.last_check) : 'Never'}
              </span>
            </Row>
            <Row label="Active Spikes">
              <span className={`font-mono text-sm font-semibold ${spikeInfo?.active_spikes > 0 ? 'text-error' : 'text-text-muted'}`}>
                {spikeInfo?.active_spikes ?? 0}
              </span>
            </Row>
          </>
        )}
      </Section>

      {/* Notifications */}
      <Section title="Notifications">
        <Row label="Telegram status">
          <span className={`text-sm font-medium ${telegramConfigured ? 'text-success' : 'text-text-muted'}`}>
            {telegramConfigured ? 'Bot connected' : 'Not configured'}
          </span>
        </Row>
        {!telegramConfigured && (
          <div className="rounded-xl p-4" style={{ background: '#F2EDE4', border: '1px solid rgba(0,0,0,0.07)' }}>
            <p className="text-sm text-text-secondary mb-2 font-semibold">To enable Telegram alerts:</p>
            <ol className="text-sm text-text-secondary space-y-1 list-decimal ml-4">
              <li>Create a bot via <span className="font-mono">@BotFather</span> on Telegram</li>
              <li>Copy the token to <span className="font-mono">.env</span> as <span className="font-mono">TELEGRAM_BOT_TOKEN</span></li>
              <li>Set <span className="font-mono">TELEGRAM_CHAT_ID</span> to your chat ID</li>
              <li>Restart the backend</li>
            </ol>
          </div>
        )}
        {telegramConfigured && (
          <Row label="Test notification">
            <button
              onClick={() => spikeCheck.mutate()}
              className="px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-cream"
              style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
            >
              Run Spike Check
            </button>
          </Row>
        )}
      </Section>

      {/* Scheduled Jobs */}
      <Section title="Scheduled Jobs">
        {isLoading ? (
          <SkeletonBlock className="h-32 w-full" />
        ) : !scheduler?.jobs?.length ? (
          <p className="text-sm text-text-muted">No jobs registered</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
                  <th className="text-left py-2 text-xs font-semibold text-text-muted uppercase">Job ID</th>
                  <th className="text-left py-2 text-xs font-semibold text-text-muted uppercase">Next Run</th>
                </tr>
              </thead>
              <tbody>
                {scheduler.jobs.map((job) => (
                  <tr key={job.job_id} className="border-b" style={{ borderColor: 'rgba(0,0,0,0.05)' }}>
                    <td className="py-2 font-mono text-xs text-text-primary">{job.job_id}</td>
                    <td className="py-2 text-xs text-text-muted font-mono">
                      {job.next_run ? new Date(job.next_run).toLocaleTimeString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* Next Runs */}
      {scheduler?.next_runs?.length > 0 && (
        <Section title="Upcoming Runs">
          <div className="space-y-2">
            {scheduler.next_runs.map((run, i) => (
              <div key={i} className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">{run.desk_name || run.job_id}</span>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs text-text-muted">{run.minutes_from_now} min</span>
                  <span className="font-mono text-xs text-text-primary">
                    {run.next_run_ist ? new Date(run.next_run_ist).toLocaleTimeString() : '—'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}
