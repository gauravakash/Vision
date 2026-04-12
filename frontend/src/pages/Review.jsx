import { useState, useEffect, useRef } from 'react'
import { CheckCheck, XCircle, Play, Inbox } from 'lucide-react'
import { usePendingDrafts, useApproveDraft, useAbortDraft } from '../hooks/useDrafts'
import { useDesks } from '../hooks/useDesks'
import { useAccounts } from '../hooks/useAccounts'
import { useRunDesk } from '../hooks/useAgent'
import DraftCard from '../components/draft/DraftCard'
import { SkeletonCard } from '../components/ui/Spinner'
import toast from 'react-hot-toast'

export default function Review() {
  const { data: pending = [], isLoading, dataUpdatedAt } = usePendingDrafts()
  const desksData = useDesks().data || {}
  const desks = Array.isArray(desksData?.items) ? desksData.items : []
  const accountsData = useAccounts().data || {}
  const accounts = Array.isArray(accountsData?.items) ? accountsData.items : []
  const approve = useApproveDraft()
  const abort = useAbortDraft()
  const runDesk = useRunDesk()

  const [filterDesk, setFilterDesk] = useState('')
  const [filterAccount, setFilterAccount] = useState('')
  const [filterType, setFilterType] = useState('')

  // Toast on new drafts arriving
  const prevCount = useRef(pending.length)
  useEffect(() => {
    const curr = Array.isArray(pending) ? pending.length : 0
    if (curr > prevCount.current && prevCount.current > 0) {
      const diff = curr - prevCount.current
      toast.success(`${diff} new draft${diff !== 1 ? 's' : ''} arrived`)
    }
    prevCount.current = curr
  }, [pending.length, dataUpdatedAt])

  const filtered = pending.filter((d) => {
    if (filterDesk && d.desk_id !== Number(filterDesk)) return false
    if (filterAccount && d.account_id !== Number(filterAccount)) return false
    if (filterType && d.content_type !== filterType) return false
    return true
  })

  async function approveAll() {
    if (!filtered.length) return
    const ids = filtered.map((d) => d.id)
    toast.loading(`Approving ${ids.length}…`, { id: 'approve-all' })
    await Promise.allSettled(ids.map((id) => approve.mutateAsync(id)))
    toast.dismiss('approve-all')
    toast.success(`Approved ${ids.length} drafts`)
  }

  async function abortAll() {
    if (!filtered.length) return
    const ids = filtered.map((d) => d.id)
    toast.loading(`Aborting ${ids.length}…`, { id: 'abort-all' })
    await Promise.allSettled(ids.map((id) => abort.mutateAsync(id)))
    toast.dismiss('abort-all')
    toast.success(`Aborted ${ids.length} drafts`)
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-text-primary">Review Queue</h1>
          <p className="text-sm text-text-muted mt-0.5">{filtered.length} draft{filtered.length !== 1 ? 's' : ''} pending</p>
        </div>
        {filtered.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={abortAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors hover:bg-red-50"
              style={{ borderColor: 'rgba(192,57,43,0.3)', color: '#C0392B' }}
            >
              <XCircle size={14} /> Abort All
            </button>
            <button
              onClick={approveAll}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white"
              style={{ background: '#1A7A4A' }}
            >
              <CheckCheck size={14} /> Approve All
            </button>
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <select
          value={filterDesk}
          onChange={(e) => setFilterDesk(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}
        >
          <option value="">All Desks</option>
          {desks.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <select
          value={filterAccount}
          onChange={(e) => setFilterAccount(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}
        >
          <option value="">All Accounts</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.handle}</option>)}
        </select>
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm border bg-card text-text-secondary outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}
        >
          <option value="">All Types</option>
          {['text', 'photo', 'video', 'thread', 'reply', 'quote_rt'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <Inbox size={40} className="mx-auto text-text-muted mb-3 opacity-40" />
          <h3 className="font-display text-lg font-semibold text-text-primary">No pending drafts</h3>
          <p className="text-sm text-text-muted mt-1 mb-6">Run the agent on a desk to generate new drafts</p>
          <div className="flex flex-wrap justify-center gap-2">
            {desks.slice(0, 4).map((d) => (
              <button
                key={d.id}
                onClick={() => runDesk.mutate({ deskId: d.id, data: {} })}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-sm font-medium border transition-colors hover:bg-orange/5"
                style={{ borderColor: 'rgba(255,92,26,0.3)', color: '#FF5C1A' }}
              >
                <Play size={13} /> {d.name}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map((draft) => (
            <DraftCard key={draft.id} draft={draft} />
          ))}
        </div>
      )}
    </div>
  )
}
