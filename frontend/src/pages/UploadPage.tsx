import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../lib/api'

type Upload = { name: string; progress: number; invoiceId?: number; error?: string }

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg'

export default function UploadPage() {
  const [uploads, setUploads] = useState<Upload[]>([])
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  function update(index: number, changes: Partial<Upload>) {
    setUploads((current) => current.map((u, i) => (i === index ? { ...u, ...changes } : u)))
  }

  async function start(files: FileList | null) {
    if (!files?.length) return
    const offset = uploads.length
    const list = Array.from(files)
    setUploads((current) => [...current, ...list.map((file) => ({ name: file.name, progress: 0 }))])
    // One at a time: the free server and the free LLM tier both prefer it.
    for (const [i, file] of list.entries()) {
      try {
        const { id } = await api.upload(file, (progress) => update(offset + i, { progress }))
        update(offset + i, { progress: 1, invoiceId: id })
      } catch (error) {
        update(offset + i, { error: error instanceof Error ? error.message : 'Upload failed' })
      }
    }
    queryClient.invalidateQueries({ queryKey: ['invoices'] })
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    start(event.dataTransfer.files)
  }

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-xl font-semibold">Upload invoices</h1>
      <p className="mt-1 text-sm text-gray-600">
        PDF, PNG or JPEG, up to 10 pages. Each file is read by the AI in the background (about
        10 seconds), then checked by the validation rules.
      </p>

      <div
        role="button"
        tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`mt-6 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-14 text-center ${
          dragging ? 'border-gray-900 bg-gray-100' : 'border-gray-300 bg-white'
        }`}
      >
        <p className="font-medium">Drop invoice files here</p>
        <p className="mt-1 text-sm text-gray-500">or click to choose files</p>
        <input
          ref={input}
          type="file"
          accept={ACCEPTED}
          multiple
          className="hidden"
          onChange={(e) => {
            start(e.target.files)
            e.target.value = '' // allow choosing the same file again
          }}
        />
      </div>

      {uploads.length > 0 && (
        <ul className="mt-6 divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
          {uploads.map((upload, i) => (
            <li key={i} className="flex items-center gap-4 px-4 py-3 text-sm">
              <span className="min-w-0 flex-1 truncate">{upload.name}</span>
              {upload.error ? (
                <span className="text-red-700">{upload.error}</span>
              ) : upload.invoiceId !== undefined ? (
                <Link to={`/invoices/${upload.invoiceId}`} className="font-medium underline">
                  Open invoice #{upload.invoiceId}
                </Link>
              ) : (
                <span className="flex w-40 items-center gap-2">
                  <span className="h-1.5 flex-1 overflow-hidden rounded bg-gray-200">
                    <span
                      className="block h-full bg-gray-900"
                      style={{ width: `${Math.round(upload.progress * 100)}%` }}
                    />
                  </span>
                  <span className="w-9 text-right text-gray-500">{Math.round(upload.progress * 100)}%</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-6 text-xs text-gray-500">
        Demo note: the free AI tier may use uploaded content to improve its models. Use sample or
        synthetic invoices, not real customer documents.
      </p>
    </div>
  )
}
