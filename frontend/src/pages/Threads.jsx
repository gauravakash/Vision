import { useState, useEffect } from 'react'
import { Layers, ChevronDown, ChevronUp, Check, X, RefreshCw, Play, Zap } from 'lucide-react'
import { useAccounts } from '../hooks/useAccounts'
import { useDesks } from '../hooks/useDesks'
import { getThreadTypes, buildThread, buildThreadsForDesk, getThread, runDeskThreads, approveDraft, abortDraft } from '../api/client'
import toast from 'react-hot-toast'

const TYPE_COLORS = {
  analysis:  { bg: 'rgba(24,95,165,0.08)',  border: 'rgba(24,95,165,0.25)',  text: '#185FA5' },
  explainer: { bg: 'rgba(26,122,74,0.08)',  border: 'rgba(26,122,74,0.25)',  text: '#1A7A4A' },
  story:     { bg: 'rgba(128,0,128,0.08)', border: 'rgba(128,0,128,0.25)', text: '#800080' },
  hot_takes: { bg: 'rgba(192,57,43,0.08)', border: 'rgba(192,57,43,0.25)', text: '#C0392B' },
  data_story:{ bg: 'rgba(255,92,26,0.08)', border: 'rgba(255,92,26,0.25)', text: '#FF5C1A' },
}

function ThreadTypePill({ type, selected, onClick }) {
  const label = type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  const col = TYPE_COLORS[type] || TYPE_COLORS.analysis
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all"
      style={selected
        ? { background: col.bg, color: col.text, border: `1px solid ${col.border}` }
        : { background: 'transparent', color: '#5C4D42', borderColor: 'rgba(0,0,0,0.1)' }}
    >
      {label}
    </button>
  )
}

function TweetCard({ tweet, index, total, onApprove, onAbort }) {
  const charOk = tweet.char_count <= 240
  const isApproved = tweet.status === 'approved'
  const isAborted = tweet.status === 'aborted'

  return (
    <div
      className="rounded-2xl border p-4 space-y-2 transition-all"
      style={{
        borderColor: isApproved ? 'rgba(26,122,74,0.3)' : isAborted ? 'rgba(192,57,43,0.2)' : 'rgba(0,0,0,0.08)',
        background: isApproved ? 'rgba(26,122,74,0.04)' : isAborted ? 'rgba(192,57,43,0.03)' : 'white',
      }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-text-muted">{index}/{total}</span>
          {tweet.role && (
            <span className="text-xs px-2 py-0.5 rounded-full font-medium"
              style={{ background: 'rgba(0,0,0,0.05)', color: '#5C4D42' }}>
              {tweet.role.replace(/_/g, ' ')}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-mono" style={{ color: charOk ? '#888' : '#C0392B' }}>
            {tweet.char_count || (tweet.text || '').length} chars
          </span>
          {!isApproved && !isAborted && (
            <>
              <button onClick={() => onApprove(tweet.id || tweet.draft_id)}
                className="p-1 rounded-lg hover:bg-green-50 transition-colors" title="Approve">
                <Check size={14} style={{ color: '#1A7A4A' }} />
              </button>
              <button onClick={() => onAbort(tweet.id || tweet.draft_id)}
                className="p-1 rounded-lg hover:bg-red-50 transition-colors" title="Abort">
                <X size={14} style={{ color: '#C0392B' }} />
              </button>
            </>
          )}
          {isApproved && <span className="text-xs font-semibold" style={{ color: '#1A7A4A' }}>✓ Approved</span>}
          {isAborted && <span className="text-xs font-semibold" style={{ color: '#C0392B' }}>✗ Aborted</span>}
        </div>
      </div>
      <p className="text-sm text-text-primary leading-relaxed">{tweet.final_text || tweet.text}</p>
    </div>
  )
}

function ThreadResult({ result, onRefresh }) {
  const [expanded, setExpanded] = useState(true)
  const [tweets, setTweets] = useState(result.tweets || [])

  async function handleApprove(draftId) {
    try {
      await approveDraft(draftId)
      setTweets((prev) => prev.map((t) => (t.id === draftId || t.draft_id === draftId) ? { ...t, status: 'approved' } : t))
      toast.success('Tweet approved')
    } catch { toast.error('Failed to approve') }
  }

  async function handleAbort(draftId) {
    try {
      await abortDraft(draftId)
      setTweets((prev) => prev.map((t) => (t.id === draftId || t.draft_id === draftId) ? { ...t, status: 'aborted' } : t))
      toast.success('Tweet aborted')
    } catch { toast.error('Failed to abort') }
  }

  async function handleApproveAll() {
    for (const t of tweets.filter((t) => t.status === 'pending')) {
      await handleApprove(t.id || t.draft_id)
    }
  }

  async function handleAbortAll() {
    for (const t of tweets.filter((t) => t.status === 'pending')) {
      await handleAbort(t.id || t.draft_id)
    }
  }

  if (!result.success) {
    return (
      <div className="rounded-2xl border p-4 space-y-1" style={{ borderColor: 'rgba(192,57,43,0.2)', background: 'rgba(192,57,43,0.04)' }}>
        <p className="text-sm font-semibold text-error">Build failed</p>
        <p className="text-xs text-text-muted">{result.error}</p>
      </div>
    )
  }

  const col = TYPE_COLORS[result.thread_type] || TYPE_COLORS.analysis
  const pendingCount = tweets.filter((t) => t.status === 'pending').length

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(0,0,0,0.08)' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b cursor-pointer"
        style={{ background: '#FDFAF6', borderColor: 'rgba(0,0,0,0.07)' }}
        onClick={() => setExpanded((e) => !e)}>
        <div className="flex items-center gap-2.5">
          <span className="text-lg">🧵</span>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-text-primary">@{result.account_handle}</span>
              <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: col.bg, color: col.text, border: `1px solid ${col.border}` }}>
                {result.thread_type?.replace(/_/g, ' ')}
              </span>
            </div>
            <p className="text-xs text-text-muted">#{result.topic} · {result.tweet_count} tweets</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {pendingCount > 0 && (
            <span className="text-xs font-mono px-1.5 py-0.5 rounded-full text-white"
              style={{ background: '#FF5C1A', fontSize: 10 }}>
              {pendingCount} pending
            </span>
          )}
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>

      {/* Tweets */}
      {expanded && (
        <div className="p-4 space-y-3" style={{ background: 'white' }}>
          {tweets.map((t, i) => (
            <TweetCard
              key={t.id || t.draft_id || i}
              tweet={t}
              index={i + 1}
              total={tweets.length}
              onApprove={handleApprove}
              onAbort={handleAbort}
            />
          ))}

          {pendingCount > 0 && (
            <div className="flex gap-2 pt-1">
              <button onClick={handleApproveAll}
                className="flex-1 py-2 rounded-xl text-sm font-semibold text-white"
                style={{ background: '#1A7A4A' }}>
                ✓ Approve All
              </button>
              <button onClick={handleAbortAll}
                className="flex-1 py-2 rounded-xl text-sm font-semibold border"
                style={{ color: '#C0392B', borderColor: 'rgba(192,57,43,0.3)' }}>
                ✗ Abort All
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Threads() {
  const { data: accounts = [] } = useAccounts()
  const { data: desks = [] } = useDesks()

  const [threadTypes, setThreadTypes] = useState({})
  const [selectedType, setSelectedType] = useState('analysis')
  const [tweetCount, setTweetCount] = useState(5)
  const [selectedAccounts, setSelectedAccounts] = useState([])
  const [selectedDesk, setSelectedDesk] = useState(null)
  const [topicTag, setTopicTag] = useState('')
  const [topicContext, setTopicContext] = useState('')
  const [topicVolume, setTopicVolume] = useState('')
  const [building, setBuilding] = useState(false)
  const [results, setResults] = useState([])
  const [runMode, setRunMode] = useState('single') // 'single' | 'desk'

  useEffect(() => {
    getThreadTypes().then(setThreadTypes).catch(() => {})
  }, [])

  useEffect(() => {
    if (accounts.length > 0 && selectedAccounts.length === 0) {
      setSelectedAccounts([accounts[0].id])
    }
  }, [accounts, selectedAccounts.length])

  useEffect(() => {
    if (desks.length > 0 && !selectedDesk) {
      setSelectedDesk(desks[0].id)
    }
  }, [desks, selectedDesk])

  function toggleAccount(id) {
    setSelectedAccounts((prev) =>
      prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]
    )
  }

  async function handleBuild() {
    if (!topicTag.trim()) { toast.error('Enter a topic tag'); return }
    if (runMode === 'single' && selectedAccounts.length === 0) { toast.error('Select at least one account'); return }
    if (runMode === 'desk' && !selectedDesk) { toast.error('Select a desk'); return }

    const topic = {
      tag: topicTag.trim(),
      context: topicContext.trim() || null,
      volume_display: topicVolume.trim() || null,
      status: 'stable',
    }

    setBuilding(true)
    setResults([])

    try {
      if (runMode === 'desk') {
        const desk = desks.find((d) => d.id === selectedDesk)
        const res = await buildThreadsForDesk(selectedDesk, { topic, thread_type: selectedType })
        toast.success(`Built ${res.filter((r) => r.success).length} thread(s)`)
        setResults(res)
      } else {
        const newResults = []
        for (const accountId of selectedAccounts) {
          const account = accounts.find((a) => a.id === accountId)
          const deskId = account?.desk_ids?.[0] || desks[0]?.id
          if (!deskId) { toast.error(`No desk for account ${account?.handle}`); continue }
          const res = await buildThread({ account_id: accountId, desk_id: deskId, topic, thread_type: selectedType, tweet_count: tweetCount })
          newResults.push(res)
        }
        toast.success(`Built ${newResults.filter((r) => r.success).length} thread(s)`)
        setResults(newResults)
      }
    } catch (e) {
      toast.error(e.message || 'Build failed')
    } finally {
      setBuilding(false)
    }
  }

  const typeKeys = Object.keys(threadTypes)

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-text-primary">Thread Builder</h1>
          <p className="text-sm text-text-muted mt-0.5">Multi-tweet threads for deep engagement</p>
        </div>
        <button
          onClick={handleBuild}
          disabled={building}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: '#FF5C1A' }}
        >
          {building ? <RefreshCw size={15} className="animate-spin" /> : <Play size={15} />}
          {building ? 'Building…' : 'Build Thread'}
        </button>
      </div>

      {/* Builder form */}
      <div className="rounded-2xl border p-5 space-y-5" style={{ background: 'white', borderColor: 'rgba(0,0,0,0.08)' }}>
        {/* Topic */}
        <div className="space-y-2">
          <label className="text-xs font-semibold text-text-muted uppercase tracking-wide">Topic</label>
          <input
            value={topicTag}
            onChange={(e) => setTopicTag(e.target.value)}
            placeholder="#TopicTag or keyword…"
            className="w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-orange"
            style={{ borderColor: 'rgba(0,0,0,0.1)', fontFamily: 'Satoshi' }}
          />
          <div className="grid grid-cols-2 gap-3">
            <input
              value={topicContext}
              onChange={(e) => setTopicContext(e.target.value)}
              placeholder="Context (optional)"
              className="w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-orange"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}
            />
            <input
              value={topicVolume}
              onChange={(e) => setTopicVolume(e.target.value)}
              placeholder="Volume (e.g. 2.1M tweets)"
              className="w-full px-3 py-2 rounded-lg border text-sm outline-none focus:border-orange"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}
            />
          </div>
        </div>

        {/* Thread type */}
        <div>
          <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Thread Type</label>
          <div className="flex flex-wrap gap-2">
            {typeKeys.length === 0
              ? ['analysis', 'explainer', 'story', 'hot_takes', 'data_story'].map((t) => (
                <ThreadTypePill key={t} type={t} selected={selectedType === t} onClick={() => setSelectedType(t)} />
              ))
              : typeKeys.map((t) => (
                <ThreadTypePill key={t} type={t} selected={selectedType === t} onClick={() => setSelectedType(t)} />
              ))}
          </div>
          {threadTypes[selectedType] && (
            <p className="text-xs text-text-muted mt-1.5">{threadTypes[selectedType].description}</p>
          )}
        </div>

        {/* Tweet count */}
        <div>
          <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">
            Tweet Count: {tweetCount}
          </label>
          <div className="flex gap-2">
            {[4, 5, 6, 7, 8].map((n) => (
              <button key={n} onClick={() => setTweetCount(n)}
                className="w-9 h-9 rounded-xl text-sm font-semibold border transition-all"
                style={tweetCount === n
                  ? { background: '#FF5C1A', color: 'white', border: '1px solid #FF5C1A' }
                  : { borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
                {n}
              </button>
            ))}
          </div>
        </div>

        {/* Mode toggle */}
        <div>
          <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Build Mode</label>
          <div className="flex gap-2">
            {['single', 'desk'].map((m) => (
              <button key={m} onClick={() => setRunMode(m)}
                className="px-3 py-1.5 rounded-xl text-sm font-semibold border transition-all capitalize"
                style={runMode === m
                  ? { background: 'rgba(255,92,26,0.1)', color: '#FF5C1A', border: '1px solid rgba(255,92,26,0.3)' }
                  : { borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
                {m === 'desk' ? 'All Desk Accounts' : 'Select Accounts'}
              </button>
            ))}
          </div>
        </div>

        {/* Account / desk selector */}
        {runMode === 'single' ? (
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Accounts</label>
            <div className="space-y-1.5">
              {accounts.map((a) => (
                <label key={a.id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedAccounts.includes(a.id)}
                    onChange={() => toggleAccount(a.id)}
                    className="accent-orange w-3.5 h-3.5"
                  />
                  <span className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                    style={{ background: a.color }}>
                    {a.initials}
                  </span>
                  <span className="text-sm text-text-primary">{a.handle}</span>
                  <span className="text-xs text-text-muted">{a.tone} · {a.style}</span>
                </label>
              ))}
            </div>
          </div>
        ) : (
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Desk</label>
            <div className="flex flex-wrap gap-2">
              {desks.map((d) => (
                <button key={d.id} onClick={() => setSelectedDesk(d.id)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm border transition-all"
                  style={selectedDesk === d.id
                    ? { background: d.color + '18', color: d.color, border: `1px solid ${d.color}44` }
                    : { borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
                  <span className="w-2 h-2 rounded-full" style={{ background: d.color }} />
                  {d.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-3">
          <h2 className="font-display text-lg font-semibold text-text-primary">
            Thread Results
          </h2>
          {results.map((r, i) => (
            <ThreadResult key={r.run_id || i} result={r} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {results.length === 0 && !building && (
        <div className="text-center py-12">
          <Layers size={36} className="mx-auto text-text-muted mb-3 opacity-25" />
          <p className="text-sm text-text-muted">Configure and build a thread to see results here.</p>
        </div>
      )}

      {building && (
        <div className="flex items-center justify-center gap-3 py-8">
          <div className="w-5 h-5 rounded-full border-2 border-orange border-t-transparent animate-spin" />
          <p className="text-sm text-text-muted">Building thread(s)… this takes 10-20 seconds</p>
        </div>
      )}
    </div>
  )
}
