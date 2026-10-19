import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { api } from '../lib/api'

/** "Try demo invoices": adds six bundled sample invoices to your account and reads them. */
export default function DemoButton({ className = '' }: { className?: string }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const load = useMutation({
    mutationFn: api.loadDemo,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      navigate('/invoices', {
        state: {
          notice:
            result.created > 0
              ? `Added ${result.created} demo invoice(s). They are being read now.`
              : 'The demo invoices are already in your account.',
        },
      })
    },
  })

  return (
    <span className={`inline-flex flex-col gap-1 ${className}`}>
      <button
        type="button"
        onClick={() => load.mutate()}
        disabled={load.isPending}
        className="rounded-md border border-gray-900 px-4 py-2 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
      >
        {load.isPending ? 'Adding demo invoices…' : 'Try demo invoices'}
      </button>
      {load.isError && <span className="text-xs text-red-700">{load.error.message}</span>}
    </span>
  )
}
