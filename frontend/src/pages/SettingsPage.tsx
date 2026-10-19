import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import type { AutoApproveSettings, SettingsResponse } from '../lib/types'

export default function SettingsPage() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  if (settings.isPending) return <p className="text-sm text-gray-500">Loading settings…</p>
  if (settings.isError) return <p className="text-sm text-red-700">{settings.error.message}</p>
  return <SettingsForm initial={settings.data} />
}

function SettingsForm({ initial }: { initial: SettingsResponse }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<AutoApproveSettings>(initial)
  const save = useMutation({
    mutationFn: api.saveSettings,
    onSuccess: (saved) => {
      queryClient.setQueryData(['settings'], saved)
      setForm(saved)
    },
  })
  const percent = (value: number) => `${Math.round(value * 100)}%`

  return (
    <form
      className="mx-auto max-w-xl space-y-6"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(form)
      }}
    >
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-gray-600">
          Auto-approve lets the agent add an invoice to the ledger without you, but only when
          every validation check passes, the AI read it with high confidence, and every line has
          a confident HSN/SAC code. Changes apply to invoices read from now on.
        </p>
      </div>

      <label className="flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4">
        <input
          type="checkbox"
          className="mt-1"
          checked={form.auto_approve_enabled}
          onChange={(e) => setForm({ ...form, auto_approve_enabled: e.target.checked })}
        />
        <span>
          <span className="font-medium">Auto-approve spotless invoices</span>
          <span className="block text-sm text-gray-600">
            Off means a person reviews every invoice (the safe default).
          </span>
        </span>
      </label>

      {(
        [
          ['auto_approve_min_confidence', 'Minimum AI reading confidence'],
          ['auto_approve_min_hsn_confidence', 'Minimum HSN/SAC code confidence'],
        ] as const
      ).map(([name, label]) => (
        <label key={name} className="block rounded-xl border border-gray-200 bg-white p-4">
          <span className="flex justify-between text-sm font-medium">
            {label}
            <span className="tabular-nums">{percent(form[name])}</span>
          </span>
          <input
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={form[name]}
            disabled={!form.auto_approve_enabled}
            onChange={(e) => setForm({ ...form, [name]: Number(e.target.value) })}
            className="mt-3 w-full"
          />
          <span className="text-xs text-gray-500">Default {percent(initial.defaults[name])}</span>
        </label>
      ))}

      <p className="text-xs text-gray-500">
        Note: the free AI model reports high confidence (95-100%) even on some misreads, so the
        validation checks, not the confidence, are the real safety net.
      </p>

      <div className="flex items-center gap-3">
        <button type="submit" disabled={save.isPending} className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {save.isPending ? 'Saving…' : 'Save settings'}
        </button>
        <button
          type="button"
          onClick={() => setForm(initial.defaults)}
          className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium hover:bg-gray-50"
        >
          Reset to defaults
        </button>
        {save.isSuccess && <span className="text-sm text-emerald-700">Saved.</span>}
        {save.isError && <span className="text-sm text-red-700">{save.error.message}</span>}
      </div>
    </form>
  )
}
