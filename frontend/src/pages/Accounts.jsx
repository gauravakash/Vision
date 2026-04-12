import { useState, useEffect } from 'react'
import { Plus, Users } from 'lucide-react'
import { useAccounts, useCreateAccount, useUpdateAccount, useDeleteAccount } from '../hooks/useAccounts'
import { useDesks } from '../hooks/useDesks'
import AccountCard from '../components/account/AccountCard'
import Modal from '../components/ui/Modal'
import { SkeletonCard } from '../components/ui/Spinner'
import { startLogin, checkLoginStatus, analyzeStyle, previewStyle } from '../api/client'
import { TONES, STYLES, STANCES } from '../utils/constants'
import toast from 'react-hot-toast'

const EMPTY_FORM = {
  name: '', handle: '', initials: '', color: '#FF5C1A',
  desk_ids: [], tone: 'Analytical', style: 'Thread', stance: 'Neutral',
  daily_limit: 8, tweet_length_min: 70, tweet_length_max: 200,
  persona_description: '', lingo_reference_handle: '', lingo_intensity: 50,
}

function LoginBrowser({ account, onSuccess }) {
  const [status, setStatus] = useState('idle') // idle | waiting | success | error
  const [sessionId, setSessionId] = useState(null)
  const [handle, setHandle] = useState('')
  const [pollRef, setPollRef] = useState(null)

  async function openBrowser() {
    if (!account?.id) { toast.error('Save the account first'); return }
    setStatus('waiting')
    try {
      const res = await startLogin(account.id)
      setSessionId(res.session_id)
      // Poll every 3s
      const interval = setInterval(async () => {
        try {
          const s = await checkLoginStatus(res.session_id)
          if (s.cookies_saved || s.status === 'success') {
            clearInterval(interval)
            setStatus('success')
            setHandle(s.handle || account.handle || '')
            onSuccess?.()
          }
        } catch { clearInterval(interval); setStatus('error') }
      }, 3000)
      setPollRef(interval)
    } catch (e) { setStatus('error'); toast.error(e.message) }
  }

  useEffect(() => () => clearInterval(pollRef), [pollRef])

  return (
    <div className="rounded-2xl border overflow-hidden" style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
      {/* Browser chrome */}
      <div className="px-4 py-3 flex items-center gap-2 border-b" style={{ background: '#E8E0D4', borderColor: 'rgba(0,0,0,0.08)' }}>
        <div className="flex gap-1.5">
          <span className="w-3 h-3 rounded-full bg-error/70" />
          <span className="w-3 h-3 rounded-full bg-warning/70" />
          <span className="w-3 h-3 rounded-full bg-success/70" />
        </div>
        <div className="flex-1 mx-3 bg-white rounded-md px-3 py-1 text-xs text-text-muted font-mono">
          x.com/login
        </div>
      </div>
      {/* Body */}
      <div className="p-8 text-center min-h-32 flex flex-col items-center justify-center" style={{ background: '#FDFAF6' }}>
        {status === 'idle' && (
          <>
            <div className="w-10 h-10 rounded-xl bg-black flex items-center justify-center mb-3">
              <span className="text-white font-bold text-lg">𝕏</span>
            </div>
            <p className="text-sm text-text-secondary mb-4">A browser window will open for X login</p>
            <button
              onClick={openBrowser}
              className="px-5 py-2 rounded-xl text-sm font-semibold text-white"
              style={{ background: '#FF5C1A' }}
            >
              Open Login Browser
            </button>
          </>
        )}
        {status === 'waiting' && (
          <>
            <div className="w-8 h-8 rounded-full border-2 border-orange border-t-transparent animate-spin mb-3" />
            <p className="text-sm text-text-primary font-medium">Waiting for login…</p>
            <p className="text-xs text-text-muted mt-1">Complete login in the browser window</p>
          </>
        )}
        {status === 'success' && (
          <>
            <div className="w-10 h-10 rounded-full flex items-center justify-center mb-3" style={{ background: 'rgba(26,122,74,0.12)' }}>
              <span style={{ color: '#1A7A4A', fontSize: 20 }}>✓</span>
            </div>
            <p className="text-sm font-semibold text-success">Login successful</p>
            {handle && <p className="text-xs text-text-muted mt-1 font-mono">{handle}</p>}
          </>
        )}
        {status === 'error' && (
          <p className="text-sm text-error">Login failed — try again</p>
        )}
      </div>
    </div>
  )
}

function AccountForm({ initial, desks, onSave, onCancel }) {
  const [form, setForm] = useState({ ...EMPTY_FORM, ...initial })
  const [step, setStep] = useState(initial?.id ? 1 : 0) // 0=login, 1=configure
  const [lingoAnalyzing, setLingoAnalyzing] = useState(false)
  const [lingoPreviewing, setLingoPreviewing] = useState(false)
  const [lingoProfile, setLingoProfile] = useState(null)
  const [lingoPreviewTweet, setLingoPreviewTweet] = useState(null)

  function set(key, val) { setForm((f) => ({ ...f, [key]: val })) }

  async function handleAnalyzeStyle() {
    const handle = (form.lingo_reference_handle || '').trim()
    if (!handle) { toast.error('Enter a reference handle first'); return }
    setLingoAnalyzing(true)
    setLingoProfile(null)
    setLingoPreviewTweet(null)
    try {
      const result = await analyzeStyle(handle)
      setLingoProfile(result.profile)
      toast.success(`Style analyzed for @${handle}`)
    } catch (e) {
      toast.error(e.message || 'Analysis failed')
    } finally {
      setLingoAnalyzing(false)
    }
  }

  async function handlePreviewTweet() {
    const handle = (form.lingo_reference_handle || '').trim()
    if (!handle) { toast.error('Enter a reference handle first'); return }
    setLingoPreviewing(true)
    setLingoPreviewTweet(null)
    try {
      const result = await previewStyle({
        reference_handle: handle,
        sample_topic: 'current events and their implications',
        intensity: form.lingo_intensity,
      })
      setLingoPreviewTweet(result.sample_tweet)
      if (!lingoProfile && result.style_profile) {
        setLingoProfile(result.style_profile)
      }
      toast.success('Preview generated')
    } catch (e) {
      toast.error(e.message || 'Preview failed')
    } finally {
      setLingoPreviewing(false)
    }
  }

  function toggleDesk(id) {
    set('desk_ids', form.desk_ids.includes(id)
      ? form.desk_ids.filter((d) => d !== id)
      : [...form.desk_ids, id])
  }

  return (
    <div className="space-y-5">
      {step === 0 && (
        <>
          <LoginBrowser account={form} onSuccess={() => setStep(1)} />
          <div className="flex items-center justify-between mt-2">
            <button onClick={onCancel} className="text-sm text-text-muted hover:text-text-primary">Cancel</button>
            <button onClick={() => setStep(1)} className="text-sm text-orange underline">Skip → configure</button>
          </div>
        </>
      )}

      {step === 1 && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name" value={form.name} onChange={(v) => set('name', v)} placeholder="John Smith" />
            <Field label="Handle" value={form.handle} onChange={(v) => set('handle', v)} placeholder="@handle" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Initials" value={form.initials} onChange={(v) => set('initials', v)} placeholder="JS" maxLength={3} />
            <div>
              <label className="text-xs font-semibold text-text-muted uppercase tracking-wide">Color</label>
              <input type="color" value={form.color} onChange={(e) => set('color', e.target.value)}
                className="mt-1 h-9 w-full rounded-lg border cursor-pointer" style={{ borderColor: 'rgba(0,0,0,0.1)' }} />
            </div>
          </div>

          {/* Desks */}
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Desks</label>
            <div className="flex flex-wrap gap-2">
              {desks.map((d) => (
                <button key={d.id} onClick={() => toggleDesk(d.id)}
                  className="px-3 py-1 rounded-full text-sm border transition-colors"
                  style={form.desk_ids.includes(d.id)
                    ? { background: d.color + '22', color: d.color, border: `1px solid ${d.color}55` }
                    : { borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
                  {d.name}
                </button>
              ))}
            </div>
          </div>

          {/* Tone */}
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Tone</label>
            <div className="flex flex-wrap gap-1.5">
              {TONES.map((t) => (
                <button key={t} onClick={() => set('tone', t)}
                  className="px-2.5 py-1 rounded-full text-xs border transition-colors"
                  style={form.tone === t
                    ? { background: 'rgba(255,92,26,0.1)', color: '#FF5C1A', border: '1px solid rgba(255,92,26,0.3)' }
                    : { borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* Style */}
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Style</label>
            <div className="flex flex-wrap gap-1.5">
              {STYLES.map((s) => (
                <button key={s} onClick={() => set('style', s)}
                  className="px-2.5 py-1 rounded-full text-xs border transition-colors"
                  style={form.style === s
                    ? { background: 'rgba(24,95,165,0.1)', color: '#185FA5', border: '1px solid rgba(24,95,165,0.3)' }
                    : { borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
                  {s}
                </button>
              ))}
            </div>
          </div>

          {/* Stance */}
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Stance</label>
            <select value={form.stance} onChange={(e) => set('stance', e.target.value)}
              className="w-full px-3 py-2 rounded-lg border text-sm bg-card outline-none focus:border-orange"
              style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
              {STANCES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* Limits */}
          <div className="grid grid-cols-3 gap-3">
            <Field label="Daily limit" type="number" value={form.daily_limit} onChange={(v) => set('daily_limit', Number(v))} />
            <Field label="Min chars" type="number" value={form.tweet_length_min} onChange={(v) => set('tweet_length_min', Number(v))} />
            <Field label="Max chars" type="number" value={form.tweet_length_max} onChange={(v) => set('tweet_length_max', Number(v))} />
          </div>

          {/* Persona */}
          <div>
            <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">Persona</label>
            <textarea value={form.persona_description} onChange={(e) => set('persona_description', e.target.value)}
              rows={2} placeholder="Short persona description…"
              className="draft-textarea" />
          </div>

          {/* Lingo Adapt */}
          <div className="rounded-2xl border p-4 space-y-3" style={{ borderColor: 'rgba(0,0,0,0.08)', background: '#FDFAF6' }}>
            <p className="text-xs font-bold text-text-muted uppercase tracking-wider">Lingo Adapt</p>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Reference Account" value={form.lingo_reference_handle} onChange={(v) => { set('lingo_reference_handle', v); setLingoProfile(null); setLingoPreviewTweet(null) }} placeholder="@naval" />
              <div>
                <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">
                  Intensity: {form.lingo_intensity}%
                </label>
                <input type="range" min={0} max={100} value={form.lingo_intensity}
                  onChange={(e) => set('lingo_intensity', Number(e.target.value))}
                  className="w-full accent-orange" />
                <div className="flex justify-between text-xs text-text-muted mt-0.5">
                  <span>Subtle</span><span>Blended</span><span>Full</span>
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleAnalyzeStyle}
                disabled={lingoAnalyzing}
                className="flex-1 py-1.5 rounded-xl text-xs font-semibold border transition-all disabled:opacity-50"
                style={{ borderColor: 'rgba(255,92,26,0.3)', color: '#FF5C1A' }}
              >
                {lingoAnalyzing ? 'Analyzing…' : 'Analyze Style'}
              </button>
              <button
                type="button"
                onClick={handlePreviewTweet}
                disabled={lingoPreviewing}
                className="flex-1 py-1.5 rounded-xl text-xs font-semibold border transition-all disabled:opacity-50"
                style={{ borderColor: 'rgba(24,95,165,0.3)', color: '#185FA5' }}
              >
                {lingoPreviewing ? 'Generating…' : 'Preview Tweet'}
              </button>
            </div>
            {lingoProfile && (
              <div className="space-y-1 pt-1 border-t text-xs" style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
                <p className="font-semibold text-text-muted uppercase tracking-wide pt-1">Style Profile</p>
                <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-text-secondary">
                  <span>Sentences: <b>{lingoProfile.avg_sentence_length}</b></span>
                  <span>Vocab: <b>{lingoProfile.vocabulary_level?.split(' - ')[0]}</b></span>
                  <span>Opens with: <b>{lingoProfile.opener_style}</b></span>
                  <span>Directness: <b>{lingoProfile.directness_level}</b></span>
                </div>
                {lingoProfile.style_summary && (
                  <p className="text-text-muted italic pt-0.5">"{lingoProfile.style_summary}"</p>
                )}
              </div>
            )}
            {lingoPreviewTweet && (
              <div className="rounded-xl border p-3 text-sm" style={{ borderColor: 'rgba(24,95,165,0.2)', background: 'rgba(24,95,165,0.04)' }}>
                <p className="text-xs font-semibold text-text-muted mb-1">This is how drafts will sound:</p>
                <p className="text-text-primary leading-relaxed">{lingoPreviewTweet}</p>
              </div>
            )}
          </div>

          <div className="flex gap-2 pt-1">
            <button onClick={onCancel} className="flex-1 py-2 rounded-xl border text-sm text-text-secondary" style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
              Cancel
            </button>
            <button
              onClick={() => onSave(form)}
              className="flex-1 py-2 rounded-xl text-sm font-semibold text-white"
              style={{ background: '#FF5C1A' }}
            >
              Save Account
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function Field({ label, value, onChange, type = 'text', placeholder, maxLength }) {
  return (
    <div>
      <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
        className="w-full px-3 py-2 rounded-lg border text-sm bg-card outline-none focus:border-orange transition-colors"
        style={{ borderColor: 'rgba(0,0,0,0.1)', fontFamily: 'Satoshi' }}
      />
    </div>
  )
}

export default function Accounts() {
  const { data: accounts = [], isLoading } = useAccounts()
  const desksData = useDesks().data || {}
  const desks = Array.isArray(desksData?.items) ? desksData.items : []
  // const { data: desks = [] } = useDesks()
  const createAcc = useCreateAccount()
  const updateAcc = useUpdateAccount()
  const deleteAcc = useDeleteAccount()

  const [modal, setModal] = useState(null) // null | { mode: 'create'|'edit', account?: obj }

  async function handleSave(form) {
    try {
      if (modal?.mode === 'edit') {
        await updateAcc.mutateAsync({ id: modal.account.id, data: form })
      } else {
        await createAcc.mutateAsync(form)
      }
      setModal(null)
    } catch { /* hook shows toast */ }
  }

  async function handleDelete(account) {
    if (!confirm(`Remove @${account.handle}?`)) return
    await deleteAcc.mutateAsync(account.id)
  }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-text-primary">Accounts</h1>
          <p className="text-sm text-text-muted mt-0.5">{accounts.length} account{accounts.length !== 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={() => setModal({ mode: 'create' })}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white"
          style={{ background: '#FF5C1A' }}
        >
          <Plus size={16} /> Add Account
        </button>
      </div>

      {isLoading ? (
        <div className="grid sm:grid-cols-2 gap-4">
          {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : accounts.length === 0 ? (
        <div className="text-center py-16">
          <Users size={40} className="mx-auto text-text-muted mb-3 opacity-30" />
          <h3 className="font-display text-lg font-semibold text-text-primary">No accounts yet</h3>
          <p className="text-sm text-text-muted mt-1 mb-4">Add your first X account to get started</p>
          <button onClick={() => setModal({ mode: 'create' })}
            className="px-4 py-2 rounded-xl text-sm font-semibold text-white" style={{ background: '#FF5C1A' }}>
            Add Account
          </button>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 gap-4">
          {accounts.map((a) => (
            <AccountCard
              key={a.id}
              account={a}
              onEdit={(acc) => setModal({ mode: 'edit', account: acc })}
              onLogin={(acc) => setModal({ mode: 'edit', account: acc })}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'Edit Account' : 'Add Account'}
        width={560}
      >
        <AccountForm
          initial={modal?.account}
          desks={desks}
          onSave={handleSave}
          onCancel={() => setModal(null)}
        />
      </Modal>
    </div>
  )
}
