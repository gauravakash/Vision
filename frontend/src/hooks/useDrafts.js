import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getPendingDrafts, getDrafts, getDraftStats,
  approveDraft, abortDraft, updateDraftText, regenerateDraft,
} from '../api/client'
import toast from 'react-hot-toast'

export function usePendingDrafts() {
  return useQuery({
    queryKey: ['drafts', 'pending'],
    queryFn: getPendingDrafts,
    refetchInterval: 10000,
    staleTime: 5000,
  })
}

export function useDrafts(params) {
  return useQuery({
    queryKey: ['drafts', params],
    queryFn: () => getDrafts(params),
    staleTime: 15000,
  })
}

export function useDraftStats() {
  return useQuery({
    queryKey: ['drafts', 'stats'],
    queryFn: getDraftStats,
    refetchInterval: 30000,
    staleTime: 15000,
  })
}

export function useApproveDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: approveDraft,
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ['drafts', 'pending'] })
      const previous = qc.getQueryData(['drafts', 'pending'])
      qc.setQueryData(['drafts', 'pending'], (old) =>
        old ? old.filter((d) => d.id !== id) : old
      )
      return { previous }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['drafts'] })
      toast.success('Draft approved')
    },
    onError: (err, _, ctx) => {
      if (ctx?.previous) qc.setQueryData(['drafts', 'pending'], ctx.previous)
      toast.error(err.message)
    },
  })
}

export function useAbortDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: abortDraft,
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ['drafts', 'pending'] })
      const previous = qc.getQueryData(['drafts', 'pending'])
      qc.setQueryData(['drafts', 'pending'], (old) =>
        old ? old.filter((d) => d.id !== id) : old
      )
      return { previous }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['drafts'] })
      toast.success('Draft aborted')
    },
    onError: (err, _, ctx) => {
      if (ctx?.previous) qc.setQueryData(['drafts', 'pending'], ctx.previous)
      toast.error(err.message)
    },
  })
}

export function useUpdateDraftText() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, text }) => updateDraftText(id, text),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drafts'] }),
    onError: (err) => toast.error(err.message),
  })
}

export function useRegenerateDraft() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: regenerateDraft,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['drafts', 'pending'] })
      toast.success('Draft regenerated')
    },
    onError: (err) => toast.error(err.message),
  })
}
