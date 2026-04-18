import { useState, useEffect } from 'react'
import { RefreshCw, Play, Zap, X } from 'lucide-react'
import { useDesks } from '../hooks/useDesks'
import { usePendingDrafts, useDraftStats } from '../hooks/useDrafts'
import { useActivity, useCurrentSpikes, useRunDesk, useRunAll, useRunSpikeCheck, useSchedulerStatus, useRefreshAllTrends } from '../hooks/useAgent'
import DeskCard from '../components/desk/DeskCard'
import { SkeletonCard } from '../components/ui/Spinner'
import { timeAgo, formatVolume } from '../utils/formatters'
import { ACTIVITY_COLORS } from '../utils/constants'
import { spikeResponse } from '../api/client'
import toast from 'react-hot-toast'

function StatCard({ label, value, color, sub }) {
  return (
    <div className="bg-card rounded-2xl border p-4" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <p className="text-xs text-text-muted uppercase tracking-wide font-semibold mb-1">{label}</p>
      <p className="font-display text-2xl font-semibold" style={{ color: color || '#1A1208' }}>{value ?? '—'}</p>
      {sub && <p className="text-xs text-text-muted mt-0.5">{sub}</p>}
    </div>
  )
}

function ActivityFeed({ events }) {
  if (!events?.length) {
    return <p className="text-sm text-text-muted text-center py-6">No activity yet</p>
  }
  return (
    <div className="space-y-1">
      {events.map((e) => (
        <div key={e.id} className="flex items-start gap-3 py-2 px-3 rounded-xl hover:bg-cream/50 transition-colors">
          <span
            className="mt-1.5 flex-shrink-0 w-2 h-2 rounded-full"
            style={{ background: ACTIVITY_COLORS[e.event_type] || ACTIVITY_COLORS.default }}
          />
          <div className="flex-1 min-w-0">
            <p className="text-sm text-text-primary leading-snug">{e.message}</p>
            <p className="text-xs text-text-muted mt-0.5">{e.time_ago}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Home() {
  const { data: desks = [], isLoading: desksLoading } = useDesks()
  const { data: pending = [] } = usePendingDrafts()
  const { data: stats } = useDraftStats()
  const { data: spikes = [] } = useCurrentSpikes()
  const { data: activity = [] } = useActivity({ limit: 15 })
  const { data: scheduler } = useSchedulerStatus()
  const runDesk = useRunDesk()
  const runAll = useRunAll()
  const spikeCheck = useRunSpikeCheck()
  const refreshTrends = useRefreshAllTrends()

  const [dismissedSpikes, setDismissedSpikes] = useState(new Set())
  const [countdown, setCountdown] = useState('—')

  // Countdown to next scheduled run
  useEffect(() => {
    if (!scheduler?.next_runs?.length) return
    const target = new Date(scheduler.next_runs[0]?.next_run_ist)
    const interval = setInterval(() => {
      const diff = Math.floor((target - new Date()) / 1000)
      if (diff <= 0) { setCountdown('Now'); return }
      const m = Math.floor(diff / 60)
      const s = diff % 60
      setCountdown(`${m}:${s.toString().padStart(2, '0')}`)
    }, 1000)
    return () => clearInterval(interval)
  }, [scheduler])

  const activeSpikes = spikes.filter((s) => !dismissedSpikes.has(s.topic_tag))
  const activeDesks = desks.filter((d) => d.is_active)
  const pendingCount = Array.isArray(pending) ? pending.length : 0

  return (
    <div className="p-6 space-y-6 max-w-screen-2xl mx-auto">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-text-primary">Command Center</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Next run in <span className="font-mono">{countdown}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refreshTrends.mutate()}
            disabled={refreshTrends.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium border transition-colors hover:bg-cream"
            style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
          >
            <RefreshCw size={15} className={refreshTrends.isPending ? 'animate-spin' : ''} />
            Fetch Trends
          </button>
          <button
            onClick={() => spikeCheck.mutate()}
            disabled={spikeCheck.isPending}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium border transition-colors hover:bg-cream"
            style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
          >
            <Zap size={15} className={spikeCheck.isPending ? 'animate-pulse' : ''} />
            Spike Check
          </button>
          <button
            onClick={() => runAll.mutate()}
            disabled={runAll.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: '#FF5C1A' }}
          >
            <Play size={15} />
            Run All Now
          </button>
        </div>
      </div>

      {/* Spike banners */}
      {activeSpikes.slice(0, 3).map((spike) => (
        <div
          key={spike.topic_tag}
          className="spike-card rounded-2xl border-2 p-4"
          style={{ borderColor: 'rgba(192,57,43,0.5)', background: 'rgba(192,57,43,0.03)' }}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Zap size={20} style={{ color: '#C0392B', flexShrink: 0, marginTop: 1 }} />
              <div>
                <p className="font-semibold text-sm text-text-primary">
                  Spike: <span className="font-mono">{spike.topic_tag}</span> on {spike.desk_name}
                </p>
                <p className="text-sm text-text-secondary mt-0.5">
                  +{Math.round(spike.spike_percent)}% in 15 min · {spike.volume_display}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={async () => {
                  try {
                    await spikeResponse(spike.desk_id, spike.topic_tag)
                    toast.success('Spike drafts generating…')
                  } catch (e) { toast.error(e.message) }
                }}
                className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white"
                style={{ background: '#C0392B' }}
              >
                Draft Now
              </button>
              <button
                onClick={() => setDismissedSpikes((s) => new Set([...s, spike.topic_tag]))}
                className="p-1.5 rounded-lg text-text-muted hover:bg-red-50 transition-colors"
              >
                <X size={16} />
              </button>
            </div>
          </div>
        </div>
      ))}

      {/* Stats strip */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <StatCard label="Active Desks" value={activeDesks.length} />
        <StatCard
          label="Spikes Now"
          value={spikes.length}
          color={spikes.length > 0 ? '#C0392B' : undefined}
        />
        <StatCard
          label="Drafts Pending"
          value={pendingCount}
          color={pendingCount > 0 ? '#C67B00' : undefined}
        />
        <StatCard
          label="Today Approved"
          value={stats?.approved ?? 0}
          color="#1A7A4A"
          sub={stats?.approval_rate != null ? `${stats.approval_rate}% rate` : undefined}
        />
        <StatCard
          label="Global Mode"
          value={scheduler?.jobs?.filter(j => j.job_id.startsWith('desk')).length > 0 ? 'Auto' : 'Manual'}
          color="#FF5C1A"
        />
      </div>

      {/* Desk grid */}
      <div>
        <h2 className="font-display text-base font-semibold text-text-primary mb-3">Desk Monitoring</h2>
        {desksLoading ? (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
            {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
            {activeDesks.map((desk) => (
              <DeskCard
                key={desk.id}
                desk={desk}
                isSpiking={spikes.some((s) => s.desk_id === desk.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Activity feed */}
      <div className="bg-card rounded-2xl border" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
        <div className="px-5 py-4 border-b flex items-center justify-between" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
          <h2 className="font-display text-base font-semibold text-text-primary">Live Activity</h2>
          <span className="flex items-center gap-1.5 text-xs text-text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
            Auto-refreshing
          </span>
        </div>
        <div className="p-3">
          <ActivityFeed events={activity} />
        </div>
      </div>
    </div>
  )
}
