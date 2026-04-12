import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import {
  getWatchlistAccounts,
  addWatchlistAccount,
  deleteWatchlistAccount,
  getOpportunities,
  triggerMonitor,
  postReplyDraft,
  getPostLog,
  seedWatchlists,
} from '../api/client'
import { useDesks } from '../hooks/useDesks'
import { timeAgo } from '../utils/formatters'
import Badge from '../components/ui/Badge'
import { SkeletonBlock } from '../components/ui/Spinner'

// ─── Sub-components ──────────────────────────────────────────────────────────

function ScoreBar({ score }) {
  const color = score >= 75 ? '#C0392B' : score >= 50 ? '#FF5C1A' : score >= 25 ? '#F39C12' : '#95A5A6'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full" style={{ background: 'rgba(0,0,0,0.07)' }}>
        <div
          className="h-1.5 rounded-full transition-all"
          style={{ width: `${score}%`, background: color }}
        />
      </div>
      <span className="font-mono text-xs font-semibold" style={{ color }}>{score}</span>
    </div>
  )
}

function ActionBadge({ action }) {
  const cfg = {
    immediate:    { label: '🔥 Immediate',    bg: 'rgba(192,57,43,0.12)',  color: '#C0392B' },
    batched:      { label: '📌 Batched',      bg: 'rgba(255,92,26,0.1)',   color: '#FF5C1A' },
    low_priority: { label: '📎 Low priority', bg: 'rgba(0,0,0,0.06)',      color: '#5C4D42' },
    skip:         { label: '⏭ Skip',          bg: 'rgba(0,0,0,0.04)',      color: '#95A5A6' },
  }[action] || { label: action, bg: 'rgba(0,0,0,0.06)', color: '#5C4D42' }
  return (
    <span className="px-2 py-0.5 rounded-full text-xs font-semibold" style={{ background: cfg.bg, color: cfg.color }}>
      {cfg.label}
    </span>
  )
}

// ─── Watchlist Panel ──────────────────────────────────────────────────────────

function WatchlistPanel({ deskId, deskName }) {
  const qc = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ handle: '', priority: 'medium' })

  const { data: accounts = [], isLoading } = useQuery({
    queryKey: ['watchlist', deskId],
    queryFn: () => getWatchlistAccounts(deskId),
    enabled: !!deskId,
    staleTime: 30000,
  })

  const addMut = useMutation({
    mutationFn: () => addWatchlistAccount({ desk_id: deskId, handle: form.handle.replace('@', ''), priority: form.priority }),
    onSuccess: () => {
      qc.invalidateQueries(['watchlist', deskId])
      setForm({ handle: '', priority: 'medium' })
      setShowAdd(false)
      toast.success('Account added to watchlist')
    },
    onError: (e) => toast.error(e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => deleteWatchlistAccount(id),
    onSuccess: () => {
      qc.invalidateQueries(['watchlist', deskId])
      toast.success('Removed from watchlist')
    },
    onError: (e) => toast.error(e.message),
  })

  const monitorMut = useMutation({
    mutationFn: () => triggerMonitor(deskId),
    onSuccess: (data) => {
      qc.invalidateQueries(['opportunities'])
      toast.success(`Monitor complete — ${data.created ?? 0} new opportunities`)
    },
    onError: (e) => toast.error(e.message),
  })

  const priorityColor = { high: '#C0392B', medium: '#FF5C1A', low: '#95A5A6' }

  return (
    <div className="bg-card rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'rgba(0,0,0,0.07)', background: '#FDFAF6' }}>
        <div>
          <p className="text-sm font-semibold text-text-primary">{deskName}</p>
          <p className="text-xs text-text-muted">{accounts.length} accounts monitored</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => monitorMut.mutate()}
            disabled={monitorMut.isPending}
            className="px-2.5 py-1 rounded-lg text-xs border transition-colors hover:bg-cream disabled:opacity-40"
            style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
          >
            {monitorMut.isPending ? 'Scanning…' : 'Scan Now'}
          </button>
          <button
            onClick={() => setShowAdd(!showAdd)}
            className="px-2.5 py-1 rounded-lg text-xs text-white transition-colors"
            style={{ background: '#FF5C1A' }}
          >
            + Add
          </button>
        </div>
      </div>

      {showAdd && (
        <div className="px-4 py-3 border-b flex gap-2" style={{ borderColor: 'rgba(0,0,0,0.07)', background: '#F8F5F0' }}>
          <input
            value={form.handle}
            onChange={(e) => setForm((f) => ({ ...f, handle: e.target.value }))}
            placeholder="@handle"
            className="flex-1 px-3 py-1.5 rounded-lg text-sm border bg-white outline-none focus:border-orange"
            style={{ borderColor: 'rgba(0,0,0,0.1)' }}
          />
          <select
            value={form.priority}
            onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}
            className="px-2 py-1.5 rounded-lg text-sm border bg-white outline-none"
            style={{ borderColor: 'rgba(0,0,0,0.1)' }}
          >
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <button
            onClick={() => addMut.mutate()}
            disabled={!form.handle || addMut.isPending}
            className="px-3 py-1.5 rounded-lg text-sm text-white disabled:opacity-40"
            style={{ background: '#FF5C1A' }}
          >
            Save
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="p-4 space-y-2">
          {[1, 2, 3].map((i) => <SkeletonBlock key={i} className="h-4 w-full" />)}
        </div>
      ) : accounts.length === 0 ? (
        <p className="px-4 py-6 text-sm text-text-muted text-center">No accounts — add some or seed defaults</p>
      ) : (
        <div>
          {accounts.map((acc) => (
            <div key={acc.id} className="flex items-center gap-3 px-4 py-2.5 border-b last:border-b-0" style={{ borderColor: 'rgba(0,0,0,0.05)' }}>
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: priorityColor[acc.priority] || '#95A5A6' }}
              />
              <span className="text-sm font-medium text-text-primary flex-1 truncate">{acc.x_handle}</span>
              {acc.is_verified && <span className="text-xs text-blue-500">✓</span>}
              <span className="text-xs text-text-muted font-mono">{acc.total_replies_sent} sent</span>
              <span className="text-xs text-text-muted">{acc.last_checked ? timeAgo(acc.last_checked) : '—'}</span>
              <button
                onClick={() => deleteMut.mutate(acc.id)}
                className="text-xs text-text-muted hover:text-error transition-colors"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Opportunity Card ─────────────────────────────────────────────────────────

function OpportunityCard({ opp, onPost }) {
  const statusColor = {
    pending:  { bg: 'rgba(255,92,26,0.1)',   text: '#FF5C1A' },
    notified: { bg: 'rgba(52,152,219,0.1)',   text: '#3498DB' },
    expired:  { bg: 'rgba(0,0,0,0.06)',       text: '#95A5A6' },
    acted:    { bg: 'rgba(26,122,74,0.1)',    text: '#1A7A4A' },
  }[opp.status] || { bg: 'rgba(0,0,0,0.06)', text: '#5C4D42' }

  return (
    <div className="bg-card rounded-2xl border p-4 space-y-3" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {opp.author_handle && (
              <span className="text-xs font-medium text-text-primary">{opp.author_handle}</span>
            )}
            <ActionBadge action={opp.action} />
            <span
              className="px-1.5 py-0.5 rounded text-xs font-semibold"
              style={{ background: statusColor.bg, color: statusColor.text }}
            >
              {opp.status}
            </span>
          </div>
          <p className="text-sm text-text-secondary line-clamp-3">{opp.tweet_text}</p>
        </div>
      </div>

      <ScoreBar score={opp.virality_score} />

      <div className="flex items-center justify-between">
        <span className="text-xs text-text-muted">{timeAgo(opp.created_at)}</span>
        <div className="flex items-center gap-2">
          <a
            href={opp.tweet_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs px-2 py-1 rounded-lg border transition-colors hover:bg-cream"
            style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
          >
            View Tweet
          </a>
          {opp.status === 'pending' && (
            <button
              onClick={() => onPost(opp)}
              className="text-xs px-2.5 py-1 rounded-lg text-white transition-colors"
              style={{ background: '#FF5C1A' }}
            >
              Draft Reply
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Post Log Row ─────────────────────────────────────────────────────────────

function PostLogRow({ log }) {
  const statusColor = {
    success:         { bg: 'rgba(26,122,74,0.1)',   text: '#1A7A4A' },
    failed:          { bg: 'rgba(192,57,43,0.1)',   text: '#C0392B' },
    captcha_blocked: { bg: 'rgba(243,156,18,0.1)',  text: '#F39C12' },
    session_expired: { bg: 'rgba(0,0,0,0.06)',      text: '#95A5A6' },
  }[log.status] || { bg: 'rgba(0,0,0,0.06)', text: '#5C4D42' }

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b last:border-b-0" style={{ borderColor: 'rgba(0,0,0,0.05)' }}>
      <span className="text-xs font-mono uppercase text-text-muted w-10">{log.post_type}</span>
      <span className="flex-1 text-xs text-text-secondary truncate">{log.text_posted?.slice(0, 70)}…</span>
      <span className="font-mono text-xs text-text-muted">{log.playwright_duration_ms ? `${log.playwright_duration_ms}ms` : '—'}</span>
      <span
        className="px-1.5 py-0.5 rounded text-xs font-semibold"
        style={{ background: statusColor.bg, color: statusColor.text }}
      >
        {log.status}
      </span>
      <span className="text-xs text-text-muted whitespace-nowrap">{timeAgo(log.posted_at)}</span>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function Engagement() {
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState('opportunities')
  const [filterDesk, setFilterDesk] = useState('')
  const [filterStatus, setFilterStatus] = useState('pending')

  const desksData = useDesks().data || {}
  const desks = Array.isArray(desksData?.items) ? desksData.items : []

  const { data: opportunities = [], isLoading: oppsLoading } = useQuery({
    queryKey: ['opportunities', filterDesk, filterStatus],
    queryFn: () => getOpportunities({
      ...(filterDesk && { desk_id: Number(filterDesk) }),
      ...(filterStatus && { status: filterStatus }),
    }),
    staleTime: 15000,
    refetchInterval: 30000,
  })

  const { data: postLog = [], isLoading: logLoading } = useQuery({
    queryKey: ['post-log'],
    queryFn: () => getPostLog({ limit: 50 }),
    staleTime: 15000,
    enabled: activeTab === 'postlog',
  })

  const seedMut = useMutation({
    mutationFn: seedWatchlists,
    onSuccess: (data) => {
      qc.invalidateQueries(['watchlist'])
      toast.success(`Seeded ${data.total_added} accounts across ${data.desks_processed} desks`)
    },
    onError: (e) => toast.error(e.message),
  })

  const handlePost = (opp) => {
    toast(`Opening opportunity #${opp.id} for reply`, { icon: '📌' })
  }

  const tabs = [
    { id: 'opportunities', label: 'Opportunities' },
    { id: 'watchlists',    label: 'Watchlists' },
    { id: 'postlog',       label: 'Post Log' },
  ]

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-text-primary">Engagement</h1>
          <p className="text-sm text-text-muted mt-0.5">Watchlist monitoring, reply opportunities, auto-posting</p>
        </div>
        <button
          onClick={() => seedMut.mutate()}
          disabled={seedMut.isPending}
          className="px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-cream disabled:opacity-40"
          style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
        >
          {seedMut.isPending ? 'Seeding…' : 'Seed Defaults'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-xl" style={{ background: 'rgba(0,0,0,0.04)' }}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={[
              'flex-1 py-1.5 rounded-lg text-sm font-medium transition-all',
              activeTab === tab.id
                ? 'bg-white text-text-primary shadow-sm'
                : 'text-text-muted hover:text-text-secondary',
            ].join(' ')}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Opportunities Tab */}
      {activeTab === 'opportunities' && (
        <div className="space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap gap-2 items-center">
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}
            >
              <option value="">All Statuses</option>
              {['pending', 'notified', 'expired', 'acted'].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select
              value={filterDesk}
              onChange={(e) => setFilterDesk(e.target.value)}
              className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}
            >
              <option value="">All Desks</option>
              {desks.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <span className="text-xs text-text-muted ml-auto">{opportunities.length} total</span>
          </div>

          {/* Opportunity grid */}
          {oppsLoading ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="bg-card rounded-2xl border p-4 space-y-3" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
                  <SkeletonBlock className="h-4 w-32" />
                  <SkeletonBlock className="h-3 w-full" />
                  <SkeletonBlock className="h-3 w-3/4" />
                  <SkeletonBlock className="h-1.5 w-full rounded-full" />
                </div>
              ))}
            </div>
          ) : opportunities.length === 0 ? (
            <div className="bg-card rounded-2xl border p-10 text-center" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
              <p className="text-text-muted text-sm">No opportunities found</p>
              <p className="text-xs text-text-muted mt-1">Run "Scan Now" on a watchlist desk or wait for the next 30-min cycle</p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {opportunities.map((opp) => (
                <OpportunityCard key={opp.id} opp={opp} onPost={handlePost} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Watchlists Tab */}
      {activeTab === 'watchlists' && (
        <div className="space-y-4">
          {desks.length === 0 ? (
            <p className="text-sm text-text-muted">No desks configured</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {desks.map((desk) => (
                <WatchlistPanel key={desk.id} deskId={desk.id} deskName={desk.name} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Post Log Tab */}
      {activeTab === 'postlog' && (
        <div className="bg-card rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
          <div className="px-4 py-3 border-b" style={{ borderColor: 'rgba(0,0,0,0.07)', background: '#FDFAF6' }}>
            <p className="text-sm font-semibold text-text-primary">Recent Posts</p>
          </div>
          {logLoading ? (
            <div className="p-4 space-y-2">
              {[1, 2, 3, 4, 5].map((i) => <SkeletonBlock key={i} className="h-4 w-full" />)}
            </div>
          ) : postLog.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-text-muted">No posts recorded yet</p>
          ) : (
            postLog.map((log) => <PostLogRow key={log.id} log={log} />)
          )}
        </div>
      )}
    </div>
  )
}
