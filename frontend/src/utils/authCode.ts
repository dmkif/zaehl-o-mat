/**
 * The OIDC callback redirects to `/auth/token#code=<single-use code>`.
 * The fragment is never sent to a server; the code is swapped for the session
 * token via POST /api/auth/exchange (F-008).
 */
const CODE_RE = /^[A-Za-z0-9_-]{1,128}$/

export function extractLoginCode(hash: string): string | null {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash)
  const code = params.get('code')
  return code && CODE_RE.test(code) ? code : null
}
