import { useState } from 'react'
import { useDrafts, useDraftStats } from '../hooks/useDrafts'
import { useDesks } from '../hooks/useDesks'
import { useAccounts } from '../hooks/useAccounts'
import { getRunHistory } from '../api/client'
import { useQuery } from '@tanstack/react-query'
import { statusColor, truncate, timeAgo } from '../utils/formatters'
import Badge from '../components/ui/Badge'
import { SkeletonBlock } from '../components/ui/Spinner'

const PAGE_SIZE = 20

function StatCard({ label, value, color }) {
  return (
    <div className="bg-card rounded-2xl border p-4" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
      <p className="text-xs text-text-muted uppercase tracking-wide font-semibold mb-1">{label}</p>
      <p className="font-display text-2xl font-semibold" style={{ color: color || '#1A1208' }}>{value ?? '—'}</p>
    </div>
  )
}

export default function History() {
  const [page, setPage] = useState(0)
  const [filterDesk, setFilterDesk] = useState('')
  const [filterAccount, setFilterAccount] = useState('')
  const [filterStatus, setFilterStatus] = useState('')

  const { data: stats, isLoading: statsLoading } = useDraftStats()
  const { data: desks = [] } = useDesks()
  const { data: accounts = [] } = useAccounts()

  const params = {
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    ...(filterDesk && { desk_id: Number(filterDesk) }),
    ...(filterAccount && { account_id: Number(filterAccount) }),
    ...(filterStatus && { status: filterStatus }),
  }
  const { data: draftsData, isLoading } = useDrafts(params)
  const { data: runHistory = [] } = useQuery({
    queryKey: ['run-history'],
    queryFn: getRunHistory,
    staleTime: 30000,
  })

  const drafts = draftsData?.items || []
  const total = draftsData?.total || 0
  const hasNext = draftsData?.has_next || false

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <h1 className="font-display text-2xl font-semibold text-text-primary">History</h1>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {statsLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-card rounded-2xl border p-4" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
              <SkeletonBlock className="h-3 w-16 mb-3" />
              <SkeletonBlock className="h-7 w-12" />
            </div>
          ))
        ) : (
          <>
            <StatCard label="Total Today" value={stats?.total ?? 0} />
            <StatCard label="Approved" value={stats?.approved ?? 0} color="#1A7A4A" />
            <StatCard label="Aborted" value={stats?.aborted ?? 0} color="#C0392B" />
            <StatCard label="Approval Rate" value={stats?.approval_rate != null ? `${stats.approval_rate}%` : '—'} color="#FF5C1A" />
          </>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <select value={filterStatus} onChange={(e) => { setFilterStatus(e.target.value); setPage(0) }}
          className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
          <option value="">All Statuses</option>
          {['pending', 'approved', 'aborted', 'regenerated'].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select value={filterDesk} onChange={(e) => { setFilterDesk(e.target.value); setPage(0) }}
          className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
          <option value="">All Desks</option>
          {desks.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <select value={filterAccount} onChange={(e) => { setFilterAccount(e.target.value); setPage(0) }}
          className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
          <option value="">All Accounts</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.handle}</option>)}
        </select>
        <span className="ml-auto text-sm text-text-muted self-center">{total} total</span>
      </div>

      {/* Draft table */}
      <div className="bg-card rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b" style={{ borderColor: 'rgba(0,0,0,0.07)', background: '#FDFAF6' }}>
              {['Time', 'Account', 'Desk', 'Preview', 'Type', 'Status', 'Score'].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-b" style={{ borderColor: 'rgba(0,0,0,0.05)' }}>
                  {Array.from({ length: 7 }).map((_, j) => (
                    <td key={j} className="px-4 py-3"><SkeletonBlock className="h-3 w-full" /></td>
                  ))}
                </tr>
              ))
            ) : drafts.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-text-muted text-sm">No drafts found</td>
              </tr>
            ) : (
              drafts.map((d) => {
                const sc = statusColor(d.status)
                return (
                  <tr key={d.id} className="border-b hover:bg-cream/30 transition-colors"
                    style={{ borderColor: 'rgba(0,0,0,0.05)' }}>
                    <td className="px-4 py-3 text-xs text-text-muted font-mono whitespace-nowrap">{timeAgo(d.created_at)}</td>
                    <td className="px-4 py-3 text-xs font-medium">{d.account_handle}</td>
                    <td className="px-4 py-3">
                      <span className="text-xs px-2 py-0.5 rounded-full"
                        style={{ background: d.desk_color ? d.desk_color + '22' : '#F2EDE4', color: d.desk_color || '#5C4D42' }}>
                        {d.desk_name}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-text-secondary max-w-xs">
                      <span title={d.final_text}>{truncate(d.final_text, 70)}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-xs font-mono uppercase text-text-muted">{d.content_type}</span>
                    </td>
                    <td className="px-4 py-3">
                      <Badge color={sc.text} bg={sc.bg} border={sc.border}>{d.status}</Badge>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-text-muted">{d.reach_score}/10</td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
        {/* Pagination */}
        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between px-4 py-3 border-t" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
            <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
              className="px-3 py-1 rounded-lg text-sm border disabled:opacity-40 hover:bg-cream"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}>← Prev</button>
            <span className="text-xs text-text-muted">Page {page + 1} of {Math.ceil(total / PAGE_SIZE)}</span>
            <button onClick={() => setPage((p) => p + 1)} disabled={!hasNext}
              className="px-3 py-1 rounded-lg text-sm border disabled:opacity-40 hover:bg-cream"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}>Next →</button>
          </div>
        )}
      </div>

      {/* Run history */}
      {runHistory.length > 0 && (
        <div>
          <h2 className="font-display text-base font-semibold text-text-primary mb-3">Run History</h2>
          <div className="bg-card rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
            {runHistory.map((run, i) => (
              <div key={run.run_id}
                className={['flex items-center gap-4 px-4 py-3', i > 0 ? 'border-t' : ''].join(' ')}
                style={{ borderColor: 'rgba(0,0,0,0.05)' }}>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-text-primary">{run.desk_name || `Desk ${run.desk_id}`}</p>
                  <p className="text-xs text-text-muted font-mono mt-0.5">{run.run_id?.slice(0, 8)}…</p>
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-text-muted">{run.total_drafts} drafts</span>
                  <span style={{ color: '#1A7A4A' }}>✓ {run.approved || 0}</span>
                  <span style={{ color: '#C0392B' }}>✗ {run.aborted || 0}</span>
                  {run.is_spike_run && (
                    <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                      style={{ background: 'rgba(192,57,43,0.1)', color: '#C0392B' }}>SPIKE</span>
                  )}
                </div>
                <span className="text-xs text-text-muted">{timeAgo(run.started_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
