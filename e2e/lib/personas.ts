import { rig } from './rig'

/**
 * Identities of the fake IdP. The seeded users (security/rig/seed.py) are matched by `sub`, so a sign-in as
 * `admin`/`manager`/`user` yields the seeded account with its property memberships
 * (prop-a: rig-user-a = user, rig-manager = manager).
 */
export interface Persona {
  key: string
  sub: string
  username: string
  email: string
  groups: string[]
}

const p = (username: string, groups: string[], sub = `rig-sub-${username}`): Persona =>
  ({ key: username, sub, username, email: `${username}@rig.invalid`, groups })

export const PERSONAS = {
  admin: p('rig-admin', ['rig-admins']),
  manager: p('rig-manager', ['rig-managers']),
  user: p('rig-user-a', ['rig-users']),
  newcomer: p('rig-e2e-newcomer', ['rig-users'], 'rig-e2e-sub-newcomer'),
}

/** Tell the fake IdP who signs in next (global state: the suite runs with one worker). */
export async function setIdp(persona: Persona): Promise<void> {
  const res = await fetch(`${rig().oidcUrl}/_control/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      mode: 'claims', sub: persona.sub, email: persona.email,
      preferred_username: persona.username, groups: persona.groups,
    }),
  })
  if (!res.ok) throw new Error(`fake IdP control failed: ${res.status}`)
}
