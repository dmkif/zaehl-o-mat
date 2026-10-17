import { readFileSync } from 'node:fs'
import path from 'node:path'

const STATE = path.resolve(__dirname, '../../security/rig/.state')

function parseEnv(file: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const i = line.indexOf('=')
    out[line.slice(0, i)] = line.slice(i + 1)
  }
  return out
}

export interface Rig {
  /** https origin of the production-like edge (rig/browser_proxy.py): the only origin the browser uses */
  proxyUrl: string
  oidcUrl: string
  gatewayUrl: string
  superUser: string
  superPassword: string
}

let cached: Rig | undefined

/** State of the running rig (scripts/e2e.sh or security/rig/up-e2e.sh must have started it). */
export function rig(): Rig {
  if (cached) return cached
  let env: Record<string, string>
  try {
    env = parseEnv(path.join(STATE, 'rig.env'))
  } catch {
    throw new Error('rig not running: start it with scripts/e2e.sh (or security/rig/up-e2e.sh)')
  }
  if (!env.PROXY_URL) throw new Error('rig was started without the browser proxy: use security/rig/up-e2e.sh')
  cached = {
    proxyUrl: env.PROXY_URL,
    oidcUrl: env.OIDC_URL,
    gatewayUrl: env.GATEWAY_URL,
    superUser: env.SUPERADMIN_USER,
    superPassword: env.SUPERADMIN_PASSWORD,
  }
  return cached
}

/** Seeded ids (security/rig/seed.py): users, prop-a/prop-b, water meters with 3 readings each. */
export function seed(): any {
  return JSON.parse(readFileSync(path.join(STATE, 'seed.json'), 'utf8'))
}
