import type { BrowserContext, Page, Request, Response } from '@playwright/test'
import { rig } from './rig'

const JWT_RE = /eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/
const CSP_EVENT = '[csp-violation]'

/**
 * Watches every page of a context and fails the test at the end for anything the API-level rig cannot see:
 *   - Content-Security-Policy violations (console message of the browser and `securitypolicyviolation` events)
 *   - requests to http:// URLs (production is HTTPS only; the fake IdP origin is the only exception)
 *   - failed /api responses (>= 400) and network errors, unless the test declared them with `expect()`
 *   - uncaught page errors
 * It also keeps the list of visited URLs for the "token never in a URL" checks.
 */
export class Guard {
  readonly problems: string[] = []
  readonly urls: string[] = []
  private readonly allowed: { status: number; url: RegExp }[] = []
  private readonly idp = new URL(rig().oidcUrl).origin
  private readonly edge = rig().proxyUrl
  apiResponses: { url: string; status: number }[] = []

  constructor(private readonly context: BrowserContext) {
    context.addInitScript((tag) => {
      document.addEventListener('securitypolicyviolation', (e) => {
        console.error(`${tag} ${e.violatedDirective} blocked=${e.blockedURI}`)
      })
    }, CSP_EVENT)
    context.pages().forEach((p) => this.attach(p))
    context.on('page', (p) => this.attach(p))
  }

  /** Declare an expected API failure, e.g. `guard.expect(400, /\/api\/auth\/callback/)`. */
  expect(status: number, url: RegExp): void {
    this.allowed.push({ status, url })
  }

  private attach(page: Page): void {
    page.on('console', (m) => {
      if (m.type() !== 'error') return
      const t = m.text()
      if (t.startsWith(CSP_EVENT) || /Content Security Policy/i.test(t)) this.problems.push(`CSP violation: ${t}`)
    })
    page.on('pageerror', (e) => this.problems.push(`uncaught page error: ${e.message}`))
    page.on('request', (r: Request) => {
      const u = r.url()
      this.urls.push(u)
      if (u.startsWith('http://') && !u.startsWith(this.idp)) this.problems.push(`request to insecure URL: ${r.method()} ${u}`)
    })
    page.on('requestfailed', (r: Request) => {
      const err = r.failure()?.errorText ?? ''
      if (err.includes('ERR_ABORTED')) return // navigation away / cancelled by the page itself
      this.problems.push(`request failed: ${r.method()} ${r.url()} (${err})`)
    })
    page.on('response', (res: Response) => {
      const u = res.url()
      if (u.startsWith(this.edge + '/api/')) this.apiResponses.push({ url: u, status: res.status() })
      if (!u.startsWith(this.edge + '/api/') || res.status() < 400) return
      if (this.allowed.some((a) => a.status === res.status() && a.url.test(u))) return
      this.problems.push(`API error: ${res.request().method()} ${u} -> ${res.status()}`)
    })
    page.on('framenavigated', (f) => {
      if (f === page.mainFrame()) this.urls.push(f.url())
    })
  }

  /** Every URL seen (requests and navigations) is free of tokens. */
  assertNoTokenInUrls(): string[] {
    return this.urls.filter((u) => /[?&#]token=/i.test(u) || JWT_RE.test(decodeURIComponent(u)))
  }

  assertClean(): void {
    const leaks = this.assertNoTokenInUrls().map((u) => `token in URL: ${u.replace(/(eyJ[A-Za-z0-9_-]{8}).*/, '$1...')}`)
    const all = [...this.problems, ...leaks]
    if (all.length) throw new Error(`browser guard found ${all.length} problem(s):\n  - ${all.join('\n  - ')}`)
  }
}
