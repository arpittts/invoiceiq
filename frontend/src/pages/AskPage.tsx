import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../lib/api'
import type { AskResult } from '../lib/types'

const EXAMPLES = [
  'How much did I spend in total?',
  'How much GST did I pay to each vendor?',
  'Which vendor did I spend the most with?',
  'How much IGST did I pay last quarter?',
  'Which items did I buy at 18% GST?',
]

export default function AskPage() {
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState<AskResult[]>([])

  const ask = useMutation({
    mutationFn: (text: string) => api.ask(text),
    onSuccess: (result) => setHistory((items) => [result, ...items]),
    onError: (error, text) =>
      setHistory((items) => [
        { question: text, answer: null, sql: null, columns: [], rows: [], error: error.message },
        ...items,
      ]),
  })

  function submit(event: FormEvent, text = question) {
    event.preventDefault()
    if (text.trim().length < 3 || ask.isPending) return
    ask.mutate(text.trim())
    setQuestion('')
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-xl font-semibold">Ask your ledger</h1>
      <p className="mt-1 text-sm text-gray-600">
        Questions about your <b>approved</b> invoices, in plain English. The AI writes a read-only
        query that can only see your own ledger; the query and its results are always shown.
      </p>

      <form onSubmit={submit} className="mt-4 flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={500}
          placeholder="e.g. How much GST did I pay to Sharma Auto Parts last quarter?"
          aria-label="Question"
          className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm"
        />
        <button type="submit" disabled={ask.isPending || question.trim().length < 3}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          {ask.isPending ? 'Thinking…' : 'Ask'}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLES.map((example) => (
          <button key={example} type="button" disabled={ask.isPending}
            onClick={(e) => submit(e as unknown as FormEvent, example)}
            className="rounded-full border border-gray-300 bg-white px-3 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-40">
            {example}
          </button>
        ))}
      </div>

      <div className="mt-6 space-y-4">
        {history.map((item, index) => (
          <AnswerCard key={history.length - index} item={item} />
        ))}
      </div>
    </div>
  )
}

function AnswerCard({ item }: { item: AskResult }) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4">
      <p className="text-sm font-medium text-gray-900">{item.question}</p>
      {item.error ? (
        <p className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">{item.error}</p>
      ) : (
        <p className="mt-2 text-sm text-gray-800">{item.answer}</p>
      )}
      {item.sql && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-medium text-gray-600">SQL that was run</summary>
          <pre className="mt-2 overflow-x-auto rounded-md bg-gray-900 p-3 text-xs text-gray-100">{item.sql}</pre>
        </details>
      )}
      {item.columns.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="text-left text-xs">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                {item.columns.map((column) => (
                  <th key={column} className="px-2 py-1 font-medium">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {item.rows.map((row, i) => (
                <tr key={i} className="border-b border-gray-100">
                  {row.map((value, j) => (
                    <td key={j} className="px-2 py-1 tabular-nums">{value === null ? '—' : String(value)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {item.rows.length === 0 && <p className="mt-1 text-xs text-gray-500">No rows.</p>}
        </div>
      )}
    </section>
  )
}
