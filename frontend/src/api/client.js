import axios from 'axios'

const api = axios.create({
  baseURL: '',          // Vite proxy forwards /api → localhost:8000
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// Request interceptor — log in dev
api.interceptors.request.use((config) => {
  if (import.meta.env.DEV) {
    console.debug(`[API] ${config.method?.toUpperCase()} ${config.url}`)
  }
  return config
})

// Response interceptor — clean FastAPI errors
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail =
      err?.response?.data?.detail ||
      err?.response?.data?.message ||
      err?.message ||
      'An unexpected error occurred'
    const error = new Error(
      typeof detail === 'string' ? detail : JSON.stringify(detail)
    )
    error.status = err?.response?.status
    error.raw = err
    return Promise.reject(error)
  }
)

// ─── DESKS ────────────────────────────────────────────────────────────────
export const getDesks = (params) =>
  api.get('/api/desks', { params }).then((r) => r.data)

export const getDeskById = (id) =>
  api.get(`/api/desks/${id}`).then((r) => r.data)

export const createDesk = (data) =>
  api.post('/api/desks', data).then((r) => r.data)

export const updateDesk = (id, data) =>
  api.patch(`/api/desks/${id}`, data).then((r) => r.data)

export const deleteDesk = (id) =>
  api.delete(`/api/desks/${id}`)

export const toggleDeskMode = (id, mode) =>
  api.patch(`/api/desks/${id}`, { mode }).then((r) => r.data)

export const seedDesks = () =>
  api.post('/api/desks/seed').then((r) => r.data)

export const getDeskTrends = (id, limit = 10) =>
  api.get(`/api/agent/trends/${id}`, { params: { limit } }).then((r) => r.data)

// ─── ACCOUNTS ─────────────────────────────────────────────────────────────
export const getAccounts = (params) =>
  api.get('/api/accounts', { params }).then((r) => r.data)

export const getAccountById = (id) =>
  api.get(`/api/accounts/${id}`).then((r) => r.data)

export const createAccount = (data) =>
  api.post('/api/accounts', data).then((r) => r.data)

export const updateAccount = (id, data) =>
  api.patch(`/api/accounts/${id}`, data).then((r) => r.data)

export const deleteAccount = (id) =>
  api.delete(`/api/accounts/${id}`)

export const getAccountStatus = (id) =>
  api.get(`/api/accounts/${id}`).then((r) => r.data)

export const disconnectAccount = (id) =>
  api.post(`/api/accounts/${id}/disconnect`).then((r) => r.data)

export const updateLingoSettings = (id, data) =>
  api.patch(`/api/accounts/${id}`, data).then((r) => r.data)

// ─── DRAFTS ───────────────────────────────────────────────────────────────
export const getDrafts = (params) =>
  api.get('/api/drafts', { params }).then((r) => r.data)

export const getPendingDrafts = () =>
  api.get('/api/drafts/pending').then((r) => r.data)

export const getDraftById = (id) =>
  api.get(`/api/drafts/${id}`).then((r) => r.data)

export const updateDraftText = (id, edited_text) =>
  api.patch(`/api/drafts/${id}`, { edited_text }).then((r) => r.data)

export const approveDraft = (id) =>
  api.post(`/api/drafts/${id}/approve`).then((r) => r.data)

export const abortDraft = (id) =>
  api.post(`/api/drafts/${id}/abort`).then((r) => r.data)

export const regenerateDraft = (id) =>
  api.post(`/api/drafts/${id}/regenerate`).then((r) => r.data)

export const deleteDraft = (id) =>
  api.delete(`/api/drafts/${id}`)

export const getDraftStats = () =>
  api.get('/api/drafts/stats/today').then((r) => r.data)

// ─── AGENT ────────────────────────────────────────────────────────────────
export const runDesk = (deskId, data = {}) =>
  api
    .post(`/api/agent/run-desk/${deskId}`, null, {
      params: {
        content_type: data.content_type || 'text',
        force_topic: data.force_topic || undefined,
      },
    })
    .then((r) => r.data)

export const runAll = (mode) =>
  api.post('/api/agent/run-all', null, { params: mode ? { mode } : {} }).then((r) => r.data)

export const spikeResponse = (deskId, topic) =>
  api.post(`/api/agent/spike-response/${deskId}`, { topic }).then((r) => r.data)

export const getDeskTrendsLive = (deskId, fresh = false, limit = 10) =>
  api
    .get(`/api/agent/trends/${deskId}`, { params: { fresh, limit } })
    .then((r) => r.data)

export const agentRegenerateDraft = (draftId) =>
  api.post(`/api/agent/regenerate/${draftId}`).then((r) => r.data)

export const getActivity = (params) =>
  api.get('/api/agent/activity', { params }).then((r) => r.data)

export const getRunHistory = () =>
  api.get('/api/agent/run-history').then((r) => r.data)

// ─── SCHEDULER ────────────────────────────────────────────────────────────
export const getSchedulerStatus = () =>
  api.get('/api/scheduler/status').then((r) => r.data)

export const getNextRuns = () =>
  api.get('/api/scheduler/next-runs').then((r) => r.data)

export const toggleDeskSchedule = (deskId, mode) =>
  api.post(`/api/scheduler/toggle-desk/${deskId}`, { mode }).then((r) => r.data)

export const runSpikeCheck = () =>
  api.post('/api/scheduler/run-spike-check').then((r) => r.data)

export const getCurrentSpikes = () =>
  api.get('/api/scheduler/spikes').then((r) => r.data)

// ─── ENGAGEMENT ───────────────────────────────────────────────────────────
export const getWatchlistAccounts = (deskId) =>
  api.get('/api/engagement/watchlist', { params: { desk_id: deskId, active_only: true } }).then((r) => r.data)

export const addWatchlistAccount = (data) =>
  api.post('/api/engagement/watchlist', data).then((r) => r.data)

export const updateWatchlistAccount = (id, data) =>
  api.patch(`/api/engagement/watchlist/${id}`, data).then((r) => r.data)

export const deleteWatchlistAccount = (id) =>
  api.delete(`/api/engagement/watchlist/${id}`)

export const seedWatchlists = () =>
  api.post('/api/engagement/watchlist/seed').then((r) => r.data)

export const getOpportunities = (params) =>
  api.get('/api/engagement/opportunities', { params }).then((r) => r.data)

export const getPendingOpportunities = () =>
  api.get('/api/engagement/opportunities/pending').then((r) => r.data)

export const triggerMonitor = (deskId) =>
  api.post(`/api/engagement/monitor/${deskId}`).then((r) => r.data)

export const triggerMonitorAll = () =>
  api.post('/api/engagement/monitor-all').then((r) => r.data)

export const skipOpportunity = (oppId) =>
  api.post(`/api/engagement/opportunities/${oppId}/skip`).then((r) => r.data)

export const getEngagementStats = () =>
  api.get('/api/engagement/stats').then((r) => r.data)

export const canPost = (accountId) =>
  api.get(`/api/poster/can-post/${accountId}`).then((r) => r.data)

// ─── POSTER ───────────────────────────────────────────────────────────────
export const postDraft = (draftId) =>
  api.post(`/api/poster/post-draft/${draftId}`).then((r) => r.data)

export const postReplyDraft = (replyDraftId) =>
  api.post(`/api/poster/post-reply/${replyDraftId}`).then((r) => r.data)

export const getAccountPostStats = (accountId) =>
  api.get(`/api/poster/account-stats/${accountId}`).then((r) => r.data)

export const getPostLog = (params) =>
  api.get('/api/poster/post-log', { params }).then((r) => r.data)

// ─── LOGIN ────────────────────────────────────────────────────────────────
export const startLogin = (accountId) =>
  api.post(`/api/login/start/${accountId}`).then((r) => r.data)

export const checkLoginStatus = (sessionId) =>
  api.get(`/api/login/status/${sessionId}`).then((r) => r.data)

export const saveLoginCookies = (sessionId, accountId) =>
  api.post(`/api/login/save/${sessionId}/${accountId}`).then((r) => r.data)

export const closeLoginSession = (sessionId) =>
  api.post(`/api/login/close/${sessionId}`).then((r) => r.data)

export const testCookies = (accountId) =>
  api.post(`/api/login/test-cookies/${accountId}`).then((r) => r.data)

// ─── THREADS ──────────────────────────────────────────────────────────────
export const getThreadTypes = () =>
  api.get('/api/threads/types').then((r) => r.data)

export const buildThread = (data) =>
  api.post('/api/threads/build', data).then((r) => r.data)

export const buildThreadsForDesk = (deskId, data) =>
  api.post(`/api/threads/build-for-desk/${deskId}`, data).then((r) => r.data)

export const getThread = (runId) =>
  api.get(`/api/threads/${runId}`).then((r) => r.data)

export const runDeskThreads = (deskId) =>
  api.post(`/api/threads/run-desk/${deskId}`).then((r) => r.data)

// ─── LINGO ────────────────────────────────────────────────────────────────
export const analyzeStyle = (handle) =>
  api.post('/api/lingo/analyze', { handle }).then((r) => r.data)

export const previewStyle = (data) =>
  api.post('/api/lingo/preview', data).then((r) => r.data)

export const updateAccountLingo = (accountId, data) =>
  api.patch(`/api/lingo/account/${accountId}`, data).then((r) => r.data)

export const clearLingoCache = (handle) =>
  api.delete('/api/lingo/cache', { params: handle ? { handle } : {} }).then((r) => r.data)

export const getAccountLingo = (accountId) =>
  api.get(`/api/lingo/account/${accountId}`).then((r) => r.data)

// ─── ADMIN ────────────────────────────────────────────────────────────────
export const getAdminHealth = () =>
  api.get('/api/admin/health').then((r) => r.data)

export const getAdminMetrics = () =>
  api.get('/api/admin/metrics').then((r) => r.data)

export const getAdminCosts = () =>
  api.get('/api/admin/costs').then((r) => r.data)

export const getAdminLogs = (params) =>
  api.get('/api/admin/logs', { params }).then((r) => r.data)

export const getAdminDatabaseStats = () =>
  api.get('/api/admin/database-stats').then((r) => r.data)

export const postAdminClearCaches = () =>
  api.post('/api/admin/clear-caches').then((r) => r.data)

export const postAdminTestNotification = () =>
  api.post('/api/admin/test-notification').then((r) => r.data)

export const postAdminCleanupOldData = () =>
  api.post('/api/admin/cleanup-old-data').then((r) => r.data)

export default api
