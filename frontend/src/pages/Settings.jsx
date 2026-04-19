import { useState } from 'react'
import { useSchedulerStatus, useRunSpikeCheck } from '../hooks/useAgent'
import {
  useHealth,
  useMetrics,
  useCosts,
  useDatabaseStats,
  useLogs,
  useClearCaches,
  useCleanupData,
  useTestNotification,
} from '../hooks/useAdmin'
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

function StatusDot({ status }) {
  const map = {
    healthy: 'bg-success',
    configured: 'bg-success',
    running: 'bg-success',
    ok: 'bg-success',
    degraded: 'bg-warning',
    warning: 'bg-warning',
    not_configured: 'bg-text-muted',
    stopped: 'bg-error',
    unhealthy: 'bg-error',
    error: 'bg-error',
    critical: 'bg-error',
  }
  const color = map[status] || 'bg-text-muted'
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
}

function CheckRow({ label, check }) {
  if (!check) return null
  const status = check.status || 'unknown'
  return (
    <Row label={label}>
      <div className="flex items-center gap-2 text-sm">
        <StatusDot status={status} />
        <span className="font-medium capitalize">{status.replace('_', ' ')}</span>
        {check.response_ms != null && (
          <span className="text-text-muted text-xs font-mono">{check.response_ms}ms</span>
        )}
        {check.error && (
          <span className="text-error text-xs truncate max-w-[200px]" title={check.error}>
            {check.error}
          </span>
        )}
      </div>
    </Row>
  )
}

const LOG_LEVEL_COLORS = {
  DEBUG: '#888',
  INFO: '#27AE60',
  WARNING: '#E67E22',
  ERROR: '#E74C3C',
  CRITICAL: '#8E44AD',
}

export default function Settings() {
  const { data: scheduler, isLoading: schedulerLoading } = useSchedulerStatus()
  const spikeCheck = useRunSpikeCheck()

  const { data: health, isLoading: healthLoading } = useHealth()
  const { data: metrics, isLoading: metricsLoading } = useMetrics()
  const { data: costs } = useCosts()
  const { data: dbStats } = useDatabaseStats()

  const [logLevel, setLogLevel] = useState('WARNING')
  const [logMinutes, setLogMinutes] = useState(60)
  const { data: logs, isLoading: logsLoading } = useLogs({
    level: logLevel,
    since_minutes: logMinutes,
    lines: 100,
  })

  const clearCaches = useClearCaches()
  const cleanupData = useCleanupData()
  const testNotif = useTestNotification()

  const telegramConfigured = scheduler?.telegram?.configured
  const spikeInfo = scheduler?.spike_detector

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <h1 className="font-display text-2xl font-semibold text-text-primary">Settings</h1>

      {/* System Health */}
      <Section title="System Health">
        {healthLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonBlock key={i} className="h-4 w-full" />
            ))}
          </div>
        ) : health ? (
          <>
            <Row label="Overall">
              <div className="flex items-center gap-2">
                <StatusDot status={health.status} />
                <span className="text-sm font-semibold capitalize">{health.status}</span>
                <span className="text-xs text-text-muted font-mono">
                  up {health.uptime_human}
                </span>
              </div>
            </Row>
            <CheckRow label="Database" check={health.checks?.database} />
            <CheckRow label="xAI API" check={health.checks?.anthropic} />
            <CheckRow label="Telegram" check={health.checks?.telegram} />
            <CheckRow label="Playwright" check={health.checks?.playwright} />
            <CheckRow label="Scheduler" check={health.checks?.scheduler} />
            <CheckRow label="Disk" check={health.checks?.disk} />
          </>
        ) : (
          <p className="text-sm text-text-muted">Health check unavailable</p>
        )}
      </Section>

      {/* Cost Monitor */}
      <Section title="Cost Monitor">
        {costs ? (
          <>
            <Row label="Session total">
              <span className="font-mono text-sm font-semibold">
                ${costs.session_cost_usd?.toFixed(4)} / ₹{costs.session_cost_inr?.toFixed(2)}
              </span>
            </Row>
            <Row label="Monthly projection">
              <span className="font-mono text-sm">
                ${costs.projected_monthly_usd?.toFixed(2)} / ₹{costs.projected_monthly_inr?.toFixed(0)}
              </span>
            </Row>
            <Row label="Monthly limit">
              <span className="font-mono text-sm text-text-muted">
                ${costs.monthly_limit_usd} / alert at ${costs.alert_threshold_usd}
              </span>
            </Row>
            <Row label="Input tokens">
              <span className="font-mono text-xs text-text-muted">
                {(costs.total_input_tokens || 0).toLocaleString()}
              </span>
            </Row>
            <Row label="Output tokens">
              <span className="font-mono text-xs text-text-muted">
                {(costs.total_output_tokens || 0).toLocaleString()}
              </span>
            </Row>
          </>
        ) : (
          <p className="text-sm text-text-muted">No cost data yet</p>
        )}
      </Section>

      {/* Metrics */}
      <Section title="Metrics">
        {metricsLoading ? (
          <SkeletonBlock className="h-32 w-full" />
        ) : metrics ? (
          <>
            <Row label="API calls (session)">
              <span className="font-mono text-sm">{metrics.api_calls_total ?? 0}</span>
            </Row>
            <Row label="Drafts generated">
              <span className="font-mono text-sm">{metrics.drafts_generated ?? 0}</span>
            </Row>
            <Row label="Drafts approved">
              <span className="font-mono text-sm text-success">{metrics.drafts_approved ?? 0}</span>
            </Row>
            <Row label="Drafts aborted">
              <span className="font-mono text-sm text-error">{metrics.drafts_aborted ?? 0}</span>
            </Row>
            <Row label="Posts attempted">
              <span className="font-mono text-sm">{metrics.posts_attempted ?? 0}</span>
            </Row>
            <Row label="Posts succeeded">
              <span className="font-mono text-sm text-success">{metrics.posts_succeeded ?? 0}</span>
            </Row>
            <Row label="Scheduler runs">
              <span className="font-mono text-sm">{metrics.scheduler_runs ?? 0}</span>
            </Row>
            <Row label="Session duration">
              <span className="font-mono text-sm text-text-muted">{metrics.session_duration}</span>
            </Row>
          </>
        ) : (
          <p className="text-sm text-text-muted">No metrics yet</p>
        )}
      </Section>

      {/* Database */}
      <Section title="Database">
        {dbStats ? (
          <>
            <Row label="File size">
              <span className="font-mono text-sm">{dbStats.file_size_mb} MB</span>
            </Row>
            {Object.entries(dbStats.tables || {}).map(([table, count]) => (
              <Row key={table} label={table.replace(/_/g, ' ')}>
                <span className="font-mono text-sm">{count}</span>
              </Row>
            ))}
            <Row label="Oldest draft">
              <span className="font-mono text-xs text-text-muted">
                {dbStats.oldest_draft ? new Date(dbStats.oldest_draft).toLocaleDateString() : '—'}
              </span>
            </Row>
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => cleanupData.mutate()}
                disabled={cleanupData.isPending}
                className="px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-cream disabled:opacity-50"
                style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
              >
                {cleanupData.isPending ? 'Cleaning…' : 'Cleanup Old Data'}
              </button>
              <button
                onClick={() => clearCaches.mutate()}
                disabled={clearCaches.isPending}
                className="px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-cream disabled:opacity-50"
                style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
              >
                {clearCaches.isPending ? 'Clearing…' : 'Clear Caches'}
              </button>
            </div>
            {cleanupData.data && (
              <p className="text-xs text-success">
                Deleted {cleanupData.data.deleted_total} records
              </p>
            )}
            {clearCaches.data && (
              <p className="text-xs text-success">
                Cleared {clearCaches.data.cleared_total} cache entries
              </p>
            )}
          </>
        ) : (
          <SkeletonBlock className="h-24 w-full" />
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
          <div className="flex gap-2">
            <button
              onClick={() => testNotif.mutate()}
              disabled={testNotif.isPending}
              className="px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-cream disabled:opacity-50"
              style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
            >
              {testNotif.isPending ? 'Sending…' : 'Test Notification'}
            </button>
            <button
              onClick={() => spikeCheck.mutate()}
              className="px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-cream"
              style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
            >
              Run Spike Check
            </button>
          </div>
        )}
        {testNotif.data && (
          <p className={`text-xs ${testNotif.data.sent ? 'text-success' : 'text-error'}`}>
            {testNotif.data.sent ? 'Notification sent ✓' : 'Send failed'}
          </p>
        )}
      </Section>

      {/* Scheduled Jobs */}
      <Section title="Scheduled Jobs">
        {schedulerLoading ? (
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

      {/* Logs Viewer */}
      <Section title="Logs">
        <div className="flex gap-3 items-center flex-wrap">
          <select
            value={logLevel}
            onChange={(e) => setLogLevel(e.target.value)}
            className="text-sm border rounded-lg px-2 py-1.5 bg-card"
            style={{ borderColor: 'rgba(0,0,0,0.1)' }}
          >
            {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
          <select
            value={logMinutes}
            onChange={(e) => setLogMinutes(Number(e.target.value))}
            className="text-sm border rounded-lg px-2 py-1.5 bg-card"
            style={{ borderColor: 'rgba(0,0,0,0.1)' }}
          >
            {[15, 30, 60, 120, 360, 720, 1440].map((m) => (
              <option key={m} value={m}>Last {m >= 60 ? `${m / 60}h` : `${m}m`}</option>
            ))}
          </select>
          <span className="text-xs text-text-muted ml-auto">
            {logs?.total ?? 0} entries
          </span>
        </div>

        {logsLoading ? (
          <SkeletonBlock className="h-48 w-full" />
        ) : !logs?.entries?.length ? (
          <p className="text-sm text-text-muted">No log entries at this level</p>
        ) : (
          <div
            className="rounded-xl overflow-y-auto font-mono text-xs space-y-0.5 p-3"
            style={{ background: '#1a1a2e', maxHeight: '320px' }}
          >
            {logs.entries.map((entry, i) => (
              <div key={i} className="flex gap-2 leading-5">
                <span className="text-gray-500 shrink-0 w-[130px]">
                  {entry.timestamp?.slice(11, 19)}
                </span>
                <span
                  className="shrink-0 w-[60px]"
                  style={{ color: LOG_LEVEL_COLORS[entry.level] || '#888' }}
                >
                  {entry.level}
                </span>
                <span className="text-gray-400 shrink-0 w-[120px] truncate">{entry.module}</span>
                <span className="text-gray-200 break-all">{entry.message}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}
