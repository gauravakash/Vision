import { useState } from 'react'
import { Plus, LayoutGrid, Edit2, Play, Trash2 } from 'lucide-react'
import { useDesks, useCreateDesk, useUpdateDesk, useDeleteDesk, useToggleDeskMode } from '../hooks/useDesks'
import { useAccounts } from '../hooks/useAccounts'
import { useRunDesk } from '../hooks/useAgent'
import Modal from '../components/ui/Modal'
import { SkeletonCard } from '../components/ui/Spinner'
import { DESK_COLORS } from '../utils/constants'
import toast from 'react-hot-toast'

const EMPTY_FORM = {
  name: '', description: '', color: '#FF5C1A', topics: [],
  timing_slots: [], daily_video: 2, daily_photo: 3, daily_text: 5, mode: 'auto',
}

function TagInput({ tags, onChange, placeholder = 'Type + Enter' }) {
  const [input, setInput] = useState('')
  function add() {
    const v = input.trim()
    if (v && !tags.includes(v)) onChange([...tags, v])
    setInput('')
  }
  return (
    <div className="flex flex-wrap gap-1.5 p-2 rounded-lg border min-h-10" style={{ borderColor: 'rgba(0,0,0,0.1)' }}>
      {tags.map((t) => (
        <span key={t} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-orange/10 text-orange border border-orange/20">
          {t}
          <button onClick={() => onChange(tags.filter((x) => x !== t))} className="opacity-50 hover:opacity-100">×</button>
        </span>
      ))}
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
        placeholder={placeholder}
        className="flex-1 min-w-20 outline-none text-sm bg-transparent text-text-primary placeholder:text-text-muted"
      />
    </div>
  )
}

function DeskForm({ initial, onSave, onCancel }) {
  const [form, setForm] = useState({ ...EMPTY_FORM, ...initial })
  function set(k, v) { setForm((f) => ({ ...f, [k]: v })) }

  const total = form.daily_video + form.daily_photo + form.daily_text

  function addSlot() {
    const slot = prompt('Enter time slot (HH:MM):')
    if (!slot || !/^\d{2}:\d{2}$/.test(slot)) return
    set('timing_slots', [...form.timing_slots, slot])
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">Desk Name</label>
        <input value={form.name} onChange={(e) => set('name', e.target.value)}
          placeholder="e.g. Geopolitics"
          className="w-full px-3 py-2 rounded-lg border text-sm bg-card outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }} />
      </div>
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">Description</label>
        <input value={form.description || ''} onChange={(e) => set('description', e.target.value)}
          placeholder="Optional description"
          className="w-full px-3 py-2 rounded-lg border text-sm bg-card outline-none focus:border-orange"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }} />
      </div>

      {/* Color */}
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Color</label>
        <div className="flex gap-2">
          {DESK_COLORS.map((c) => (
            <button key={c} onClick={() => set('color', c)}
              className="w-7 h-7 rounded-full transition-transform hover:scale-110"
              style={{ background: c, outline: form.color === c ? `3px solid ${c}` : 'none', outlineOffset: 2 }} />
          ))}
        </div>
      </div>

      {/* Topics */}
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">Topics</label>
        <TagInput tags={form.topics} onChange={(v) => set('topics', v)} placeholder="Add topic + Enter" />
      </div>

      {/* Timing */}
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-1.5">
          Timing Slots (IST)
        </label>
        <div className="flex flex-wrap gap-2">
          {form.timing_slots.map((slot) => (
            <span key={slot} className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-sm font-mono border"
              style={{ borderColor: 'rgba(0,0,0,0.1)', color: '#5C4D42' }}>
              {slot}
              <button onClick={() => set('timing_slots', form.timing_slots.filter((s) => s !== slot))}
                className="opacity-40 hover:opacity-80">×</button>
            </span>
          ))}
          <button onClick={addSlot}
            className="px-2.5 py-1 rounded-lg text-sm border border-dashed transition-colors hover:bg-cream"
            style={{ borderColor: 'rgba(0,0,0,0.2)', color: '#A08880' }}>
            + Add slot
          </button>
        </div>
      </div>

      {/* Daily mix */}
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">
          Daily Mix — {total} total
          {total > 50 && <span className="ml-2 text-error">max 50</span>}
        </label>
        {[
          { key: 'daily_video', label: 'Video', color: '#185FA5' },
          { key: 'daily_photo', label: 'Photo', color: '#8E44AD' },
          { key: 'daily_text',  label: 'Text',  color: '#1A7A4A' },
        ].map(({ key, label, color }) => (
          <div key={key} className="flex items-center gap-3 mb-2">
            <span className="w-12 text-xs font-medium" style={{ color }}>{label}</span>
            <input type="range" min={0} max={20} value={form[key]}
              onChange={(e) => set(key, Number(e.target.value))}
              className="flex-1" style={{ accentColor: color }} />
            <span className="font-mono text-sm w-5 text-right">{form[key]}</span>
          </div>
        ))}
      </div>

      {/* Mode */}
      <div>
        <label className="text-xs font-semibold text-text-muted uppercase tracking-wide block mb-2">Mode</label>
        <div className="flex rounded-xl p-0.5" style={{ background: '#F2EDE4', border: '1px solid rgba(0,0,0,0.07)' }}>
          {['auto', 'manual'].map((m) => (
            <button key={m} onClick={() => set('mode', m)}
              className={['flex-1 py-1.5 rounded-lg text-sm font-medium transition-all capitalize',
                form.mode === m ? 'bg-white text-orange shadow-sm' : 'text-text-muted'].join(' ')}>
              {m}
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-2 pt-1">
        <button onClick={onCancel} className="flex-1 py-2 rounded-xl border text-sm text-text-secondary"
          style={{ borderColor: 'rgba(0,0,0,0.1)' }}>Cancel</button>
        <button onClick={() => onSave(form)}
          className="flex-1 py-2 rounded-xl text-sm font-semibold text-white"
          style={{ background: '#FF5C1A' }}>
          Save Desk
        </button>
      </div>
    </div>
  )
}

export default function Desks() {
  const { data: desksData = {}, isLoading } = useDesks()
  const desks = Array.isArray(desksData?.items) ? desksData.items : []
  const accountsData = useAccounts().data || {}
  const accounts = Array.isArray(accountsData?.items) ? accountsData.items : []
  const createDesk = useCreateDesk()
  const updateDesk = useUpdateDesk()
  const deleteDesk = useDeleteDesk()
  const runDesk = useRunDesk()

  const [modal, setModal] = useState(null)

  async function handleSave(form) {
    try {
      if (modal?.mode === 'edit') {
        await updateDesk.mutateAsync({ id: modal.desk.id, data: form })
      } else {
        await createDesk.mutateAsync(form)
      }
      setModal(null)
    } catch {}
  }

  async function handleDelete(desk) {
    if (!confirm(`Delete desk "${desk.name}"? This cannot be undone.`)) return
    await deleteDesk.mutateAsync(desk.id)
  }

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-text-primary">Desks</h1>
          <p className="text-sm text-text-muted mt-0.5">{desks.length} desk{desks.length !== 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={() => setModal({ mode: 'create' })}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold text-white"
          style={{ background: '#FF5C1A' }}
        >
          <Plus size={16} /> New Desk
        </button>
      </div>

      {isLoading ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {desks.map((desk) => {
            const deskAccounts = accounts.filter((a) => a.desk_ids?.includes(desk.id))
            return (
              <div key={desk.id} className="bg-card rounded-2xl border overflow-hidden"
                style={{ borderColor: 'rgba(0,0,0,0.07)' }}>
                <div className="px-4 pt-4 pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2.5">
                      <span className="w-3 h-3 rounded-full" style={{ background: desk.color }} />
                      <h3 className="font-display font-semibold text-sm text-text-primary">{desk.name}</h3>
                    </div>
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-medium"
                      style={desk.mode === 'auto'
                        ? { background: 'rgba(26,122,74,0.1)', color: '#1A7A4A' }
                        : { background: 'rgba(90,90,90,0.08)', color: '#5C4D42' }}>
                      {desk.mode}
                    </span>
                  </div>

                  {/* Topics */}
                  <div className="flex flex-wrap gap-1 mt-3">
                    {desk.topics?.slice(0, 5).map((t) => (
                      <span key={t} className="px-1.5 py-0.5 rounded-md text-xs"
                        style={{ background: desk.color + '18', color: desk.color }}>
                        {t}
                      </span>
                    ))}
                  </div>

                  {/* Timing */}
                  {desk.timing_slots?.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {desk.timing_slots.map((s) => (
                        <span key={s} className="px-2 py-0.5 rounded-md text-xs font-mono bg-cream text-text-muted">{s}</span>
                      ))}
                    </div>
                  )}

                  {/* Mix */}
                  <div className="flex gap-3 mt-3 text-xs text-text-muted">
                    <span>📹 {desk.daily_video}</span>
                    <span>📷 {desk.daily_photo}</span>
                    <span>📝 {desk.daily_text}</span>
                  </div>

                  {/* Account avatars */}
                  {deskAccounts.length > 0 && (
                    <div className="flex mt-3">
                      {deskAccounts.slice(0, 5).map((a, i) => (
                        <div key={a.id}
                          className="w-7 h-7 rounded-full border-2 border-white flex items-center justify-center text-white text-xs font-bold"
                          style={{ background: a.color, marginLeft: i > 0 ? -8 : 0, zIndex: deskAccounts.length - i }}>
                          {a.initials}
                        </div>
                      ))}
                      {deskAccounts.length > 5 && (
                        <div className="w-7 h-7 rounded-full border-2 border-white bg-cream flex items-center justify-center text-xs text-text-muted"
                          style={{ marginLeft: -8 }}>+{deskAccounts.length - 5}</div>
                      )}
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-1 px-4 py-2.5 border-t"
                  style={{ borderColor: 'rgba(0,0,0,0.06)', background: '#FDFAF6' }}>
                  <button onClick={() => setModal({ mode: 'edit', desk })}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs text-text-muted hover:bg-cream border border-border">
                    <Edit2 size={12} /> Edit
                  </button>
                  <button onClick={() => runDesk.mutate({ deskId: desk.id, data: {} })}
                    disabled={runDesk.isPending}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-orange hover:bg-orange/5 border border-orange/20">
                    <Play size={12} /> Run Now
                  </button>
                  <button onClick={() => handleDelete(desk)}
                    className="ml-auto flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-text-muted hover:text-error hover:bg-red-50">
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <Modal open={!!modal} onClose={() => setModal(null)}
        title={modal?.mode === 'edit' ? 'Edit Desk' : 'New Desk'} width={500}>
        <DeskForm initial={modal?.desk} onSave={handleSave} onCancel={() => setModal(null)} />
      </Modal>
    </div>
  )
}
