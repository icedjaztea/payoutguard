import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  ArrowDownLeft, ArrowRight, ArrowUpRight, Check, CircleAlert, FileText, Image,
  Inbox, LoaderCircle, Mail, RotateCcw, ScanLine, ShieldCheck, Wallet, X,
} from 'lucide-react'
import { api, demoBilled, isMock, type Batch, type Run, type Verdict } from './api'
import '@fontsource-variable/inter'
import payoutguardMark from './assets/payoutguard-mark.svg'
import brightloomMark from './assets/brightloom-mark.svg'
import '@fontsource/ibm-plex-mono/500.css'
import './styles.css'

const money = (value: number) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', maximumFractionDigits: 2, minimumFractionDigits: 0,
}).format(value)
const decisions = { PAY: 'Pay in full', SHORT_PAY: 'Short pay', HOLD_DUPLICATE: 'Duplicate hold', QUARANTINE_PAYEE: 'Payee quarantined' }
const terminal = (status: string) => ['SUCCESS', 'FAILED', 'RETURNED', 'BLOCKED', 'DENIED', 'REFUNDED', 'REVERSED'].includes(status)
const settled = (batch?: Batch) => Boolean(batch?.items.length && batch.items.every(item => terminal(item.status)))
const pause = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))
const thumbnails = import.meta.glob('./thumbnails/*.png', { eager: true, import: 'default', query: '?url' }) as Record<string, string>
const cardReasons: Partial<Record<Verdict['decision'], string>> = {
  SHORT_PAY: 'Rush surcharge needs written pre-approval.',
  HOLD_DUPLICATE: 'The same work was already paid.',
  QUARANTINE_PAYEE: 'Payee change could not be verified.',
}
const payReasons: Record<string, string> = {
  'IMG_1234.jpg': 'Three illustrations match the contracted rate.',
  'scan_0043.pdf': 'Design hours match the rate and weekly cap.',
  'final_FINAL_v2.pdf': 'Motion design hours match the rate and weekly cap.',
  'IMG_1301.jpg': 'QA hours are within the cap at the contracted rate.',
  '20261105_invoice.pdf': 'Translation matches the contracted per-word rate.',
}
const replyFile = 'scan_0042.pdf'
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : String(cause)

function Amount({ value }: { value: number }) {
  const [shown, setShown] = useState(value)
  const current = useRef(value)
  useEffect(() => {
    const from = current.current
    if (from === value) return
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
      current.current = value
      setShown(value)
      return
    }
    const start = performance.now()
    let frame: number
    const draw = (now: number) => {
      const progress = Math.min((now - start) / 320, 1)
      current.current = progress === 1 ? value : Math.round(from + (value - from) * (1 - (1 - progress) ** 3))
      setShown(current.current)
      if (progress < 1) frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [value])
  return <span aria-label={money(value)}>{money(shown)}</span>
}

function BatchList({ batch, topUp = false }: { batch: Batch; topUp?: boolean }) {
  return <section className="batch" data-testid={topUp ? 'top-up-batch' : 'payout-batch'}>
    <div className="batch-meta"><span>{topUp ? 'Top-up batch' : 'PayPal batch ID'}</span><code>{batch.batch_id}</code></div>
    <ul>{batch.items.map(item => <li key={item.file}>
      <span>{item.contractor_name}{topUp && <small> · Top-up</small>}</span>
      <strong>{money(item.amount)}</strong>
      <span className={`status ${item.status === 'SUCCESS' ? 'success' : item.status === 'PENDING' ? 'pending' : 'other'}`}>
        {item.status === 'SUCCESS' ? <Check size={11} /> : <span className="dot" />}{item.status}
      </span>
    </li>)}</ul>
  </section>
}

function Drawer({ file, verdict, run, close }: { file: string; verdict?: Verdict; run: Run; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => { ref.current?.showModal() }, [])
  const url = api.file(file)
  const [pdf, setPdf] = useState<string>()
  const [previewError, setPreviewError] = useState(false)
  const [showAllFindings, setShowAllFindings] = useState(false)
  useEffect(() => {
    if (isMock || !/\.pdf$/i.test(file)) return
    const controller = new AbortController()
    let objectUrl: string | undefined
    fetch(url, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('Preview unavailable')
        return response.blob()
      })
      .then(blob => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setPdf(objectUrl)
      })
      .catch(() => { if (!controller.signal.aborted) setPreviewError(true) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [file, url])
  const previewUrl = isMock ? url : pdf
  const appeal = run.appeal_file === file ? run.appeal : undefined
  const isImage = /\.jpe?g$/i.test(file)
  return <dialog ref={ref} className="drawer" aria-labelledby="drawer-title"
    onCancel={event => { event.preventDefault(); close() }}
    onClick={event => { if (event.target === event.currentTarget) close() }}>
    <div className="drawer-shell">
      <header className="drawer-header">
        <div><div className="eyebrow">Document review</div><h2 id="drawer-title">{file}</h2></div>
        <button data-testid="close-drawer" autoFocus className="icon-button" aria-label="Close document review" onClick={close}><X size={22} /></button>
      </header>
      <div className="drawer-grid">
        <section className="preview">
          <div className="preview-bar"><span>Source document</span><a href={url} target="_blank" rel="noreferrer">Open original <ArrowUpRight size={15} /></a></div>
          {isImage ? <img src={url} alt={`Invoice scan: ${file}`} /> : previewUrl ? <iframe src={`${previewUrl}#toolbar=0&view=FitH`} title={`PDF preview: ${file}`} /> : <p className="muted">{previewError ? 'Preview unavailable. Open the original to view it.' : 'Loading PDF…'}</p>}
        </section>
        <section className="findings">
          {verdict ? <>
            <div className="finding-title"><div><h3>{verdict.contractor_name}</h3><code>{verdict.invoice_number}</code></div><span className={`verdict ${verdict.decision}`}>{decisions[verdict.decision]}</span></div>
            <div className="detail-amount"><span>Billed <strong>{money(verdict.billed_total)}</strong></span><span>Approved <strong>{money(verdict.approved_amount)}</strong></span></div>
            {appeal && <article className="finding" data-testid="appeal-finding">
              <div className="tags"><span>AI finding</span>{appeal.clause && <span>Clause {appeal.clause}</span>}</div>
              <h4>Appeal decision · {appeal.decision === 'TOP_UP' ? `Top-up ${money(appeal.top_up_amount)}` : appeal.decision}</h4>
              <p>{appeal.reasoning}</p><blockquote>{appeal.evidence}</blockquote>
            </article>}
            {verdict.guardrail_flags.map(flag => <article className="finding" key={flag}>
              <div className="tags"><span className="rule">Rule check</span></div><blockquote>{flag}</blockquote>
            </article>)}
            {verdict.findings.slice(0, showAllFindings ? undefined : 1).map((finding, index) => <div key={finding.code}><article className="finding">
              <div className="tags"><span>AI finding</span>{finding.clause && <span>Clause {finding.clause}</span>}</div>
              <p>{finding.explanation}</p><blockquote>{finding.evidence}</blockquote>
            </article>{index === 0 && <article className="finding remittance"><h4>Remittance note preview</h4><p>{appeal?.remittance_note ?? verdict.remittance_note}</p></article>}</div>)}
            {verdict.findings.length > 1 && <button data-testid="toggle-findings" className="secondary" aria-expanded={showAllFindings} onClick={() => setShowAllFindings(value => !value)}>
              {showAllFindings ? 'Show primary finding' : `Show ${verdict.findings.length - 1} more findings`}
            </button>}
            {!verdict.findings.length && <article className="finding remittance"><h4>Remittance note preview</h4><p>{appeal?.remittance_note ?? verdict.remittance_note}</p></article>}
          </> : <p className="muted">Run the audit to see findings and the remittance note.</p>}
        </section>
      </div>
    </div>
  </dialog>
}

function App() {
  const [run, setRun] = useState<Run>()
  const [revealed, setRevealed] = useState(0)
  const [action, setAction] = useState<'audit' | 'approve' | 'appeal' | 'reset'>()
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string>()
  const [toastDismissed, setToastDismissed] = useState(false)
  const pending = useRef(false)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    api.current().then(current => current ?? api.create()).then(next => {
      setRun(next)
      if (next.files.every(file => next.audit_results[file])) setRevealed(next.files.length)
    }).catch(cause => setError(errorText(cause)))
  }, [])

  const id = run?.run_id
  const poll = Boolean((run?.batch && !settled(run.batch)) || (run?.top_up_batch && !settled(run.top_up_batch)))
  useEffect(() => {
    if (!id || !poll || action) return
    let active = true
    let timer: number
    const refresh = async () => {
      try {
        const next = await api.get(id, true)
        if (active) { setRun(next); setError('') }
      } catch (cause) {
        if (active) setError(errorText(cause))
      }
      if (active) timer = window.setTimeout(refresh, 1200)
    }
    timer = window.setTimeout(refresh, 1200)
    return () => { active = false; clearTimeout(timer) }
  }, [id, poll, action])

  async function perform(next: NonNullable<typeof action>, operation: () => Promise<void>) {
    if (pending.current) return
    pending.current = true
    setAction(next)
    setError('')
    try { await operation() }
    catch (cause) { setError(errorText(cause)) }
    finally { pending.current = false; setAction(undefined) }
  }

  async function awaitState(id: string, ready: (state: Run) => boolean) {
    const deadline = Date.now() + 120_000
    do {
      const state = await api.get(id)
      if (ready(state)) return state
      if (state.audit_error) throw new Error(state.audit_error)
      await pause(1000)
    } while (Date.now() < deadline)
    throw new Error('The run is taking longer than expected. Try again to refresh its state.')
  }

  function audit() {
    if (!run || !id) return
    void perform('audit', async () => {
      await api.audit(id)
      const next = await awaitState(id, state => state.files.every(file => Boolean(state.audit_results[file])))
      setRun(next)
      const delay = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 155
      for (let index = 1; index <= next.files.length; index++) {
        if (delay) await pause(delay)
        setRevealed(index)
      }
    })
  }

  function approve() {
    if (!id) return
    void perform('approve', async () => {
      const current = await api.get(id)
      if (!current.batch) await api.approve(id)
      setRun(await awaitState(id, state => Boolean(state.batch)))
    })
  }

  function reviewReply() {
    if (!id) return
    void perform('appeal', async () => {
      const current = await api.get(id)
      if (!current.appeal) await api.appeal(id, replyFile)
      setRun(await awaitState(id, state => Boolean(state.appeal && (state.appeal.decision !== 'TOP_UP' || state.appeal.top_up_amount <= 0 || state.top_up_batch))))
      setToastDismissed(true)
    })
  }

  function reset() {
    void perform('reset', async () => {
      const next = await api.reset()
      setSelected(undefined)
      setRevealed(0)
      setToastDismissed(false)
      setRun(next)
    })
  }

  const files = run?.files ?? []
  const audited = files.length > 0 && revealed === files.length
  const visible = files.slice(0, revealed).map(file => run?.audit_results[file]).filter(Boolean) as Verdict[]
  const billed = isMock && run ? demoBilled : visible.reduce((sum, v) => sum + v.billed_total, 0)
  const paying = visible.reduce((sum, v) => sum + v.approved_amount, 0)
  const stopped = visible.reduce((sum, v) => sum + v.billed_total - v.approved_amount, 0)
  const topUp = run?.top_up_batch?.items.filter(item => item.status === 'SUCCESS').reduce((sum, item) => sum + item.amount, 0) ?? 0
  const protectedAmount = settled(run?.batch) ? stopped - topUp : 0
  const paidReplyInvoice = run?.batch?.items.some(item => item.file === replyFile && item.status === 'SUCCESS')
  // Priya replies about her own short payment, so her item landing is the only
  // precondition. Waiting for every item meant one slow or unclaimed payee could
  // hold the reply back indefinitely.
  const replyReady = Boolean(paidReplyInvoice && !run?.appeal)
  const paidCount = run?.batch?.items.filter(item => item.status === 'SUCCESS').length ?? 0
  const payables = visible.filter(v => v.approved_amount > 0).length
  const exceptions = visible.filter(v => v.decision !== 'PAY').length

  return <div className="app">
    <header className="topbar">
      <div className="frame flex items-center justify-between">
        <div className="brand"><img src={payoutguardMark} alt="" width="28" height="28" /><span>PayoutGuard</span></div>
        <div className="workspace">
          <div className="workspace-badge"><img src={brightloomMark} alt="" width="24" height="24" /><div><strong>Brightloom Studio</strong><span>Weekly payout run · 6 Nov 2026</span></div></div>
          <span className="avatar" aria-label="Brightloom Studio workspace profile">BS</span>
        </div>
      </div>
    </header>

    <main className="frame grow">
      <section className="page-title">
        <div><div className="eyebrow">Payments / Weekly run</div><h1>Friday payout run</h1></div>
        <div className="run-controls"><span className="environment"><span className="dot" />{isMock ? 'Demo mode' : 'Sandbox'} · USD</span>
          <button data-testid="reset" className="secondary" onClick={reset} disabled={Boolean(action)}><RotateCcw size={15} /> Reset</button></div>
      </section>

      <section className="metrics" aria-label="Run totals">
        <div className="metric" data-testid="counter-billed"><span>Billed</span><strong><Amount value={billed} /></strong><small>{files.length} source documents</small></div>
        <div className="metric" data-testid="counter-paying"><span>Paying</span><strong><Amount value={paying + topUp} /></strong><small>{audited ? `${payables} payable invoices` : 'Awaiting audit'}</small></div>
        <div className="metric" data-testid="counter-stopped"><span>Stopped</span><strong><Amount value={stopped} /></strong><small>{audited ? `${exceptions} flagged at audit` : 'Awaiting audit'}</small></div>
        <div className="metric protected" data-testid="counter-protected"><span>Protected</span><strong><Amount value={protectedAmount} /></strong><small>{topUp ? 'After appeal' : settled(run?.batch) ? 'Withheld from payout' : 'After payout'}</small></div>
      </section>

      {error && <div className="error" role="alert"><CircleAlert size={17} />{error}<button data-testid="dismiss-error" className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}

      <div className="workbench">
        <section className="panel inbox" aria-labelledby="inbox-title">
          <header className="panel-heading"><div><h2 id="inbox-title"><Inbox size={18} /> Inbox</h2><p>{files.length} source documents</p></div></header>
          <ul className="documents">
            {files.map((file, index) => {
              const isImage = /\.jpe?g$/i.test(file)
              const DocIcon = isImage ? Image : FileText
              const thumbnail = isMock ? (isImage ? api.file(file) : thumbnails[`./thumbnails/${file}.png`]) : undefined
              return <li key={file}><button data-testid={`document-${file}`} onClick={() => setSelected(file)} className={`document ${selected === file ? 'selected' : ''}`} type="button">
                <span className="paper">{thumbnail ? <img src={thumbnail} alt="" /> : <DocIcon size={16} />}</span>
                <span className="document-name"><strong>{file}</strong></span>
                {index < revealed ? <Check size={15} className="document-arrow" /> : <ArrowUpRight size={15} className="document-arrow" />}
              </button></li>
            })}
          </ul>
          <div className="tray-action">
            <button data-testid="audit-run" className="primary" onClick={audit} disabled={!run || Boolean(action) || audited}>
              {action === 'audit' ? <LoaderCircle size={16} /> : audited ? <Check size={16} /> : <ScanLine size={16} />}
              {action === 'audit' ? 'Auditing…' : audited ? 'Audit complete' : "Audit this week's run"}
            </button>
          </div>
        </section>

        <section className="panel review" aria-labelledby="review-title">
          <header className="panel-heading"><div><h2 id="review-title"><ShieldCheck size={18} /> Audit verdicts</h2><p>{audited ? 'Select a verdict to review the evidence.' : 'Invoice, contract and payee checks'}</p></div><code>{revealed}/{files.length} audited</code></header>
          <div className="ledger-heading" aria-hidden="true"><span>Contractor / audit decision</span><span>Billed → Approved</span></div>
          <ul className="verdict-cards">
            {files.map((file, index) => {
              const verdict = index < revealed ? run?.audit_results[file] : undefined
              if (!verdict) return <li key={file} className="verdict-slot" aria-hidden="true" />
              const settledTopUp = file === run?.appeal_file ? topUp : 0
              const approved = verdict.approved_amount + settledTopUp
              const fullyPaid = settledTopUp > 0 && approved >= verdict.billed_total
              const clause = verdict.findings.find(finding => finding.clause)?.clause
              const reason = fullyPaid ? 'Written approval confirmed on appeal.' : cardReasons[verdict.decision] ?? payReasons[file] ?? verdict.findings[0]?.explanation
              return <li key={file}><button type="button" data-testid={`verdict-${file}`} className={`verdict-card ${fullyPaid ? 'PAY' : verdict.decision} ${selected === file ? 'selected' : ''}`} onClick={() => setSelected(file)}>
                <span className="card-top">
                  <strong>{verdict.contractor_name}</strong>
                  <span className={`verdict ${fullyPaid ? 'PAY' : verdict.decision}`}>{fullyPaid ? 'Paid in full after evidence' : decisions[verdict.decision]}</span>
                  <span className="card-amount">{money(verdict.billed_total)} <span aria-label="approved to">→</span> <b>{money(approved)}</b></span>
                </span>
                <span className="card-bottom">{clause && <span className="clause">cl. {clause}</span>}<span className="card-reason" title={reason}>{reason}</span></span>
              </button></li>
            })}
          </ul>
        </section>

        <section className="panel payout" aria-labelledby="payout-title">
          <header className="panel-heading"><div><h2 id="payout-title"><Wallet size={18} /> Payout tray</h2><p>PayPal Payouts</p></div><span className="paypal">USD</span></header>
          {run?.batch ? <div className="batch-body">
            <div className="batch-summary"><div><span>Initial payout</span><strong>{money(run.batch.items.reduce((sum, item) => sum + item.amount, 0))}</strong></div><span className="completion">{paidCount}/{run.batch.items.length} sent</span></div>
            <BatchList batch={run.batch} />
            {!replyReady && !run.appeal && !paidReplyInvoice && <p className="batch-wait" role="status">Waiting on PayPal to confirm Priya’s reduced payment. Her reply, with the written approval, arrives here next.</p>}
            {replyReady && <button data-testid="review-reply-tray" className="reply-button" onClick={reviewReply} disabled={Boolean(action)}><Mail size={16} /> {action === 'appeal' ? 'Checking approval…' : 'Review evidence'}<ArrowRight size={15} /></button>}
            {run.appeal && <div className="appeal">
              <div className="appeal-heading"><Mail size={16} /><strong>Priya’s appeal</strong>{run.appeal.clause && <span>Clause {run.appeal.clause}</span>}</div>
              <p>{run.appeal.reasoning}</p>
              <button data-testid="open-appeal-drawer" className="text-button" onClick={() => setSelected(run.appeal_file ?? replyFile)}>View evidence <ArrowUpRight size={13} /></button>
            </div>}
            {run.top_up_batch && <BatchList batch={run.top_up_batch} topUp />}
          </div> : <>
            <div className="tray-empty">
              <h3>{audited ? 'Ready for approval' : 'Awaiting audit'}</h3>
              <p>{audited ? `${payables} invoices are payable. Review any flagged documents before releasing the batch.` : 'Audit the inbox to check invoice amounts, contract terms, and payee details.'}</p>
              {audited && <div className="tray-breakdown"><span><Check size={15} /> Payable <strong>{money(paying)}</strong></span><span><CircleAlert size={15} /> Stopped <strong>{money(stopped)}</strong></span></div>}
            </div>
            <div className="tray-action">
              <button data-testid="approve-pay" className="primary" onClick={approve} disabled={!audited || Boolean(action) || !payables}>
                {action === 'approve' ? <LoaderCircle size={18} /> : <ArrowDownLeft size={18} />}
                {action === 'approve' ? 'Submitting batch…' : 'Approve & pay with PayPal'}
                <ArrowRight size={17} className="action-arrow" />
              </button>
              <p>{audited ? 'Approval submits a single PayPal sandbox batch.' : 'Payments require your approval.'}</p>
            </div>
          </>}
        </section>
      </div>
    </main>

    <footer className="frame footer">Synthetic data · PayPal sandbox · OpenAI</footer>

    {replyReady && !toastDismissed && !action && <aside className="toast" role="status">
      <span className="toast-icon"><Mail size={19} /></span><div><strong>Priya Nair replied</strong><p>Priya forwarded written PM approval.</p><button data-testid="review-evidence" className="text-button" onClick={reviewReply}>Review evidence <ArrowRight size={14} /></button></div>
      <button data-testid="dismiss-reply" className="icon-button" aria-label="Dismiss reply notification" onClick={() => setToastDismissed(true)}><X size={17} /></button>
    </aside>}
    {selected && run && <Drawer file={selected} verdict={files.indexOf(selected) < revealed ? run.audit_results[selected] : undefined} run={run} close={() => setSelected(undefined)} />}
  </div>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
