export function timeAgo(isoString) {
  if (!isoString) return ''
  const now = new Date()
  const then = new Date(isoString)
  const diff = Math.floor((now - then) / 1000)

  if (diff < 60) return 'just now'
  if (diff < 3600) {
    const m = Math.floor(diff / 60)
    return `${m} min ago`
  }
  if (diff < 86400) {
    const h = Math.floor(diff / 3600)
    return `${h} hr ago`
  }
  const d = Math.floor(diff / 86400)
  return `${d} day${d !== 1 ? 's' : ''} ago`
}

export function formatVolume(numeric) {
  if (!numeric && numeric !== 0) return 'N/A'
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(1)}M`
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(1)}K`
  return String(numeric)
}

export function formatCharCount(count) {
  if (count < 200) {
    return { count, color: '#1A7A4A', bg: 'rgba(26,122,74,0.1)', label: 'Good' }
  }
  if (count <= 250) {
    return { count, color: '#C67B00', bg: 'rgba(198,123,0,0.1)', label: 'Long' }
  }
  return { count, color: '#C0392B', bg: 'rgba(192,57,43,0.1)', label: 'Too long' }
}

export function truncate(text, maxLen = 80) {
  if (!text || text.length <= maxLen) return text || ''
  const trimmed = text.slice(0, maxLen)
  const lastSpace = trimmed.lastIndexOf(' ')
  return (lastSpace > 0 ? trimmed.slice(0, lastSpace) : trimmed) + '…'
}

export function deskColorToStyle(hexColor) {
  return {
    background: hexColor ? hexColor + '22' : 'rgba(0,0,0,0.06)',
    color: hexColor || '#5C4D42',
  }
}

export function statusColor(status) {
  switch (status) {
    case 'pending':
      return { bg: 'rgba(198,123,0,0.1)', text: '#C67B00', border: 'rgba(198,123,0,0.25)' }
    case 'approved':
      return { bg: 'rgba(26,122,74,0.1)', text: '#1A7A4A', border: 'rgba(26,122,74,0.25)' }
    case 'aborted':
      return { bg: 'rgba(192,57,43,0.1)', text: '#C0392B', border: 'rgba(192,57,43,0.25)' }
    case 'regenerated':
      return { bg: 'rgba(130,80,255,0.1)', text: '#6B3FD4', border: 'rgba(130,80,255,0.2)' }
    default:
      return { bg: 'rgba(0,0,0,0.05)', text: '#5C4D42', border: 'rgba(0,0,0,0.1)' }
  }
}

export function spikeColor(status) {
  switch (status) {
    case 'spiking': return { line: '#C0392B', bg: 'rgba(192,57,43,0.08)' }
    case 'rising':  return { line: '#FF5C1A', bg: 'rgba(255,92,26,0.08)' }
    default:        return { line: '#1A7A4A', bg: 'rgba(26,122,74,0.08)' }
  }
}
