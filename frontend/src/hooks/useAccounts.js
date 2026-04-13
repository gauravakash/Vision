import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getAccounts, getAccountById, createAccount,
  updateAccount, deleteAccount, getAccountStatus,
} from '../api/client'
import toast from 'react-hot-toast'

export function useAccounts(params) {
  return useQuery({
    queryKey: ['accounts', params],
    queryFn: () => getAccounts(params),
    staleTime: 30000,
    select: (data) => {
      // API returns { items: [...], total: N } — normalize to flat array
      if (Array.isArray(data)) return data
      if (data?.items && Array.isArray(data.items)) return data.items
      return []
    },
  })
}

export function useAccountById(id) {
  return useQuery({
    queryKey: ['accounts', id],
    queryFn: () => getAccountById(id),
    enabled: !!id,
  })
}

export function useCreateAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] })
      toast.success('Account added')
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useUpdateAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }) => updateAccount(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] })
      toast.success('Account updated')
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useDeleteAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] })
      toast.success('Account removed')
    },
    onError: (err) => toast.error(err.message),
  })
}

export function useAccountStatus(id) {
  return useQuery({
    queryKey: ['accounts', 'status', id],
    queryFn: () => getAccountStatus(id),
    enabled: !!id,
    refetchInterval: 30000,
    staleTime: 15000,
  })
}
