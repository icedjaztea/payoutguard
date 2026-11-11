import snapshot from './mock/audit_results.json'

export type Decision = 'PAY' | 'SHORT_PAY' | 'HOLD_DUPLICATE' | 'QUARANTINE_PAYEE'
type Finding = { code: string; clause: string | null; explanation: string; evidence: string }
export type Verdict = {
  contractor_id?: string
  contractor_name: string
  invoice_number: string
  billed_total: number
  approved_amount: number
  decision: Decision
  findings: Finding[]
  guardrail_flags: string[]
  remittance_note: string
}
export type Appeal = {
  decision: string
  top_up_amount: number
  clause: string | null
  reasoning: string
  evidence: string
  remittance_note: string
}
export type Payment = { file: string; contractor_name: string; amount: number; status: string }
export type Batch = { batch_id: string; items: Payment[] }
export type Run = {
  run_id: string
  files: string[]
  audit_results: Record<string, Verdict>
  batch?: Batch
  appeal_file?: string
  appeal?: Appeal
  top_up_batch?: Batch
  audit_error?: string
}

const fixture = snapshot as Record<string, Verdict | Appeal>
const order = [
  'scan_0042.pdf', 'Document (3).pdf', 'invoice.pdf', 'IMG_1234.jpg',
  'scan_0043.pdf', 'final_FINAL_v2.pdf', 'IMG_1301.jpg', '20261105_invoice.pdf',
]
const verdicts = Object.fromEntries(order.map(file => [file, fixture[file] as Verdict]))
export const demoBilled = order.reduce((sum, file) => sum + verdicts[file].billed_total, 0)

export const isMock = import.meta.env.DEV && import.meta.env.VITE_USE_MOCK === 'true'

class HttpError extends Error {
  constructor(readonly status: number, path: string) {
    super(`Server returned HTTP ${status} for ${path}.`)
  }
}

const mockDelay = () => new Promise<void>(resolve => window.setTimeout(resolve, 220))

async function request(path: string, method = 'GET'): Promise<Response> {
  const response = await fetch(path, { method, cache: 'no-store' })
  if (!response.ok) throw new HttpError(response.status, path)
  return response
}

async function read<T>(path: string, method?: string): Promise<T> {
  const response = await request(path, method)
  if (!response.headers.get('content-type')?.includes('json')) throw new Error(`Expected JSON from ${path}.`)
  return response.json()
}

type ServerItem = Partial<Omit<Payment, 'amount'>> & { sender_item_id?: string; transaction_status?: string; amount?: number | string }
type ServerBatch = { payout_batch_id: string; batch_status: string; items: ServerItem[]; items_sent?: ServerItem[] }
type ServerRun = Omit<Run, 'batch' | 'top_up_batch' | 'audit_results'> & {
  audit_results?: Run['audit_results']
  invoices?: Record<string, Verdict & { status: string; error?: string }>
  batch?: Batch | ServerBatch | null
  appeals?: Record<string, Appeal & { batch?: Batch | ServerBatch; top_up_batch?: Batch | ServerBatch }>
  top_up_batch?: Batch | ServerBatch | null
  errors?: string[]
}

function normalizeRun(raw: ServerRun): Run {
  const files = raw.files.filter(file => /\.(pdf|jpe?g)$/i.test(file))
  const auditResults = raw.audit_results ?? Object.fromEntries(
    Object.entries(raw.invoices ?? {}).filter(([, invoice]) => invoice.status === 'audited'),
  )
  const [appealFile, appeal] = Object.entries(raw.appeals ?? {})[0] ?? [raw.appeal_file, raw.appeal]
  const normalizeBatch = (batch: Batch | ServerBatch | null | undefined, topUp = false): Batch | undefined => {
    if (!batch) return
    if ('batch_id' in batch) return batch
    const candidates = topUp ? files.filter(file => file === appealFile) : files.filter(file => auditResults[file]?.approved_amount > 0)
    const known: ServerItem[] = batch.items_sent?.length ? batch.items_sent : candidates.map(file => ({
      file,
      contractor_name: auditResults[file].contractor_name,
      amount: topUp ? appeal?.top_up_amount : auditResults[file].approved_amount,
      sender_item_id: `${String(raw.files.indexOf(file) + 1).padStart(2, '0')}-${auditResults[file].contractor_id}`,
    }))
    const items = known.map((item, index) => {
      const update = batch.items?.find(entry => entry.sender_item_id === item.sender_item_id && entry.sender_item_id)
        ?? (topUp ? batch.items?.[index] : undefined)
      return {
        file: item.file ?? candidates[index],
        contractor_name: item.contractor_name ?? auditResults[candidates[index]]?.contractor_name ?? 'Contractor',
        amount: Number(update?.amount ?? item.amount ?? 0),
        status: update?.transaction_status ?? update?.status ?? item.status ?? 'PENDING',
      }
    })
    return { batch_id: batch.payout_batch_id, items }
  }
  return {
    run_id: raw.run_id, files, audit_results: auditResults, appeal_file: appealFile, appeal,
    batch: normalizeBatch(raw.batch),
    top_up_batch: normalizeBatch(raw.top_up_batch ?? raw.appeals?.[appealFile ?? '']?.top_up_batch ?? raw.appeals?.[appealFile ?? '']?.batch, true),
    audit_error: Object.values(raw.invoices ?? {}).some(invoice => invoice.status === 'error') ? raw.errors?.join(' ') || 'One or more invoices could not be audited.' : undefined,
  }
}

let mock: Run
let batchStarted = 0
let topUpStarted = 0

function newMockRun(): Run {
  mock = { run_id: crypto.randomUUID(), files: order, audit_results: {} }
  batchStarted = 0
  topUpStarted = 0
  return mock
}

function mockBatch(items: Omit<Payment, 'status'>[]): Batch {
  return {
    batch_id: `DEMO-${crypto.randomUUID().slice(0, 8).toUpperCase()}`,
    items: items.map(item => ({ ...item, status: 'PENDING' })),
  }
}

function settle(batch: Batch | undefined, started: number): Batch | undefined {
  return batch && {
    ...batch,
    items: batch.items.map(item => ({
      ...item,
      status: Date.now() - started >= 3000 ? 'SUCCESS' : 'PENDING',
    })),
  }
}

async function openRun(path: string): Promise<Run> {
  if (isMock) { await mockDelay(); return newMockRun() }
  const result = await read<{ run_id: string }>(path, 'POST')
  return api.get(result.run_id)
}

const runPath = (id: string) => `/api/runs/${encodeURIComponent(id)}`
export const api = {
  async current(): Promise<Run | undefined> {
    if (isMock) return mock ? api.get(mock.run_id) : undefined
    try {
      const current = await read<ServerRun | null>('/api/runs/current')
      return current?.run_id ? normalizeRun(current) : undefined
    } catch (error) {
      if (error instanceof HttpError && error.status === 404) return
      throw error
    }
  },
  create: () => openRun('/api/runs'),
  reset: () => openRun('/api/reset'),
  async get(id: string, refresh = false): Promise<Run> {
    if (!isMock) return normalizeRun(await read<ServerRun>(runPath(id) + (refresh ? '?refresh=true' : '')))
    if (mock.run_id !== id) throw new Error('This run has expired. Reset the demo to start again.')
    return { ...mock, batch: settle(mock.batch, batchStarted), top_up_batch: settle(mock.top_up_batch, topUpStarted) }
  },
  async audit(id: string) {
    if (!isMock) await request(`${runPath(id)}/audit`, 'POST')
    else { await mockDelay(); mock = { ...mock, audit_results: verdicts } }
  },
  async approve(id: string) {
    if (!isMock) await request(`${runPath(id)}/approve`, 'POST')
    else if (!mock.batch) {
      await mockDelay()
      batchStarted = Date.now()
      const items = Object.entries(mock.audit_results)
        .filter(([, verdict]) => verdict.approved_amount > 0)
        .map(([file, verdict]) => ({ file, contractor_name: verdict.contractor_name, amount: verdict.approved_amount }))
      mock = { ...mock, batch: mockBatch(items) }
    }
  },
  async appeal(id: string, file: string) {
    if (!isMock) await request(`${runPath(id)}/appeals/${encodeURIComponent(file)}`, 'POST')
    else if (!mock.appeal) {
      await mockDelay()
      const appeal = fixture[`_appeal_${file}`] as Appeal
      if (!appeal) throw new Error('There is no reply for this invoice.')
      topUpStarted = Date.now()
      mock = {
        ...mock,
        appeal_file: file,
        appeal,
        top_up_batch: appeal.decision === 'TOP_UP' ? mockBatch([
          { file, contractor_name: verdicts[file].contractor_name, amount: appeal.top_up_amount },
        ]) : undefined,
      }
    }
  },
  file(file: string) {
    return `/files/${encodeURIComponent(file)}`
  },
}
