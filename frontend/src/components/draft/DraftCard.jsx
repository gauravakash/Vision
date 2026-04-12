import { memo, useState, useCallback } from 'react'
import { Check, X, RefreshCw, Copy, Clock, Send } from 'lucide-react'
import { useApproveDraft, useAbortDraft, useRegenerateDraft, useUpdateDraftText } from '../../hooks/useDrafts'
import { formatCharCount, timeAgo, statusColor, deskColorToStyle } from '../../utils/formatters'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { postDraft } from '../../api/client'
import Badge from '../ui/Badge'
import toast from 'react-hot-toast'

function ReachBar({ score }) {
  const color = score >= 7 ? '#1A7A4A' : score >= 4 ? '#C67B00' : '#C0392B'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1 bg-cream rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${score * 10}%`, background: color }} />
      </div>
      <span className="font-mono text-xs" style={{ color }}>{score}/10</span>
    </div>
  )
}

const DraftCard = memo(function DraftCard({ draft }) {
  const [editedText, setEditedText] = useState(draft.edited_text || draft.text || '')
  const [isDirty, setIsDirty] = useState(false)

  const approve = useApproveDraft()
  const abort = useAbortDraft()
  const regen = useRegenerateDraft()
  const save = useUpdateDraftText()

  const charInfo = formatCharCount(editedText.length)
  const sc = statusColor(draft.status)
  const deskStyle = deskColorToStyle(draft.desk_color)

  const handleTextChange = useCallback((e) => {
    setEditedText(e.target.value)
    setIsDirty(e.target.value !== (draft.edited_text || draft.text))
  }, [draft.edited_text, draft.text])

  const handleSave = useCallback(async () => {
    await save.mutateAsync({ id: draft.id, text: editedText })
    setIsDirty(false)
    toast.success('Saved')
  }, [draft.id, editedText, save])

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(editedText)
    toast.success('Copied to clipboard')
  }, [editedText])

  const isPending = draft.status === 'pending'
  const isApproved = draft.status === 'approved'

  const qc = useQueryClient()
  const postNow = useMutation({
    mutationFn: () => postDraft(draft.id),
    onSuccess: (data) => {
      if (data.success) {
        toast.success(`Posted! ${data.tweet_url ? '→ ' + data.tweet_url : ''}`)
      } else {
        toast.error(`Post failed: ${data.error || 'unknown error'}`)
      }
      qc.invalidateQueries(['drafts'])
      qc.invalidateQueries(['pending-drafts'])
    },
    onError: (e) => toast.error(e.message),
  })

  return (
    <div
      className="bg-card rounded-2xl border overflow-hidden"
      style={{ borderColor: 'rgba(0,0,0,0.07)' }}
    >
      {/* Header */}
      <div className="px-4 pt-4 pb-3 space-y-2.5">
        <div className="flex items-start justify-between gap-3">
          {/* Account */}
          <div className="flex items-center gap-2.5">
            <div
              className="w-9 h-9 rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0"
              style={{ background: draft.account_color || '#FF5C1A', fontFamily: '"Clash Display"' }}
            >
              {(draft.account_handle || '?').replace('@', '').slice(0, 2).toUpperCase()}
            </div>
            <div>
              <p className="text-sm font-semibold text-text-primary leading-none">{draft.account_handle}</p>
              <p className="text-xs text-text-muted mt-0.5 font-mono">{timeAgo(draft.created_at)}</p>
            </div>
          </div>
          {/* Status */}
          <Badge color={sc.text} bg={sc.bg} border={sc.border}>
            {draft.status}
          </Badge>
        </div>

        {/* Desk + type chips */}
        <div className="flex flex-wrap gap-1.5">
          <span className="px-2 py-0.5 rounded-full text-xs font-medium" style={deskStyle}>
            {draft.desk_name}
          </span>
          {draft.tone_used && (
            <span className="px-2 py-0.5 rounded-full text-xs bg-cream text-text-secondary border border-border">
              {draft.tone_used}
            </span>
          )}
          {draft.style_used && (
            <span className="px-2 py-0.5 rounded-full text-xs bg-cream text-text-secondary border border-border">
              {draft.style_used}
            </span>
          )}
          <span
            className="px-2 py-0.5 rounded-full text-xs font-medium uppercase tracking-wide"
            style={{ background: 'rgba(24,95,165,0.1)', color: '#185FA5' }}
          >
            {draft.content_type}
          </span>
        </div>

        {/* Reach */}
        <ReachBar score={draft.reach_score} />
      </div>

      {/* Body — editable textarea */}
      <div className="px-4 pb-3">
        <textarea
          className="draft-textarea"
          value={editedText}
          onChange={handleTextChange}
          disabled={!isPending}
          rows={4}
        />
        <div className="flex items-center justify-between mt-1.5">
          <span className="font-mono text-xs" style={{ color: charInfo.color }}>
            {editedText.length} chars
          </span>
          {isDirty && isPending && (
            <button
              onClick={handleSave}
              className="text-xs text-orange underline hover:no-underline"
            >
              Save edit
            </button>
          )}
        </div>
      </div>

      {/* Footer actions */}
      {isPending && (
        <div
          className="flex items-center gap-2 px-4 py-3 border-t"
          style={{ borderColor: 'rgba(0,0,0,0.06)', background: '#FDFAF6' }}
        >
          <button
            onClick={() => approve.mutate(draft.id)}
            disabled={approve.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: '#1A7A4A' }}
          >
            <Check size={14} />
            Approve
          </button>
          <button
            onClick={() => abort.mutate(draft.id)}
            disabled={abort.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: '#C0392B' }}
          >
            <X size={14} />
            Abort
          </button>
          <button
            onClick={() => regen.mutate(draft.id)}
            disabled={regen.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors hover:bg-cream"
            style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}
          >
            <RefreshCw size={13} className={regen.isPending ? 'animate-spin' : ''} />
            Regen
          </button>
          <button
            onClick={handleCopy}
            className="ml-auto flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs border transition-colors hover:bg-cream"
            style={{ borderColor: 'rgba(0,0,0,0.08)', color: '#A08880' }}
          >
            <Copy size={12} />
            Copy
          </button>
        </div>
      )}

      {/* Approved — Post Now button */}
      {isApproved && (
        <div
          className="flex items-center gap-2 px-4 py-3 border-t"
          style={{ borderColor: 'rgba(0,0,0,0.06)', background: '#F0FBF4' }}
        >
          <span className="text-xs text-text-muted flex-1">
            Approved — auto-post in next cycle or post now
          </span>
          <button
            onClick={() => postNow.mutate()}
            disabled={postNow.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ background: '#1A7A4A' }}
          >
            <Send size={13} className={postNow.isPending ? 'animate-pulse' : ''} />
            {postNow.isPending ? 'Posting…' : 'Post Now'}
          </button>
        </div>
      )}
    </div>
  )
})

export default DraftCard
