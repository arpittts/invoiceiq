import { useEffect, useRef } from 'react'

export type ConfirmRequest = {
  title: string
  message: string
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
}

/**
 * An in-page "are you sure?" dialog. Unlike window.confirm, browsers can't suppress
 * it (a ticked "prevent this page from creating dialogs" made confirm() silently
 * return false, so buttons did nothing). The native <dialog> element gives focus
 * trapping, Esc to cancel and a backdrop for free.
 */
export default function ConfirmDialog({
  request,
  onClose,
}: {
  request: ConfirmRequest | null
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (request && !element.open) element.showModal()
    if (!request && element.open) element.close()
  }, [request])

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      aria-labelledby="confirm-title"
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-gray-200 p-0 shadow-xl backdrop:bg-black/30"
    >
      {request && (
        <form
          method="dialog"
          className="space-y-4 p-5"
          onSubmit={(event) => {
            event.preventDefault()
            request.onConfirm()
            onClose()
          }}
        >
          <h2 id="confirm-title" className="text-base font-semibold">
            {request.title}
          </h2>
          <p className="text-sm text-gray-600">{request.message}</p>
          <div className="flex justify-end gap-2">
            {/* Focus starts on Cancel: a stray Enter must never confirm a change. */}
            <button
              type="button"
              autoFocus
              onClick={onClose}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              className={`rounded-md px-4 py-2 text-sm font-medium text-white ${
                request.danger ? 'bg-red-700 hover:bg-red-800' : 'bg-gray-900 hover:bg-gray-800'
              }`}
            >
              {request.confirmLabel}
            </button>
          </div>
        </form>
      )}
    </dialog>
  )
}
