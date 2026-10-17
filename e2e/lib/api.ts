import { request, APIRequestContext } from '@playwright/test'
import { rig } from './rig'

/** Thin API client (through the HTTPS edge) used to build test data; tests then look at it in the browser. */
export class Api {
  private constructor(readonly ctx: APIRequestContext, private readonly token: string) {}

  static async superadmin(): Promise<Api> {
    const r = rig()
    const ctx = await request.newContext({ baseURL: r.proxyUrl, ignoreHTTPSErrors: true })
    const res = await ctx.post('/api/auth/superadmin-login', { data: { username: r.superUser, password: r.superPassword } })
    if (!res.ok()) throw new Error(`superadmin login failed: ${res.status()}`)
    return new Api(ctx, (await res.json()).access_token)
  }

  private headers() {
    return { Authorization: `Bearer ${this.token}` }
  }

  async call<T = any>(method: 'get' | 'post' | 'patch' | 'delete', url: string, data?: unknown, okStatus?: number[]): Promise<T> {
    const res = await this.ctx[method](url, { headers: this.headers(), data, maxRedirects: 0 })
    const ok = okStatus ?? [200, 201, 204]
    if (!ok.includes(res.status())) throw new Error(`${method.toUpperCase()} ${url} -> ${res.status()} ${await res.text()}`)
    return (res.status() === 204 ? undefined : await res.json().catch(() => undefined)) as T
  }

  createProperty(name: string) {
    return this.call<{ id: string }>('post', '/api/properties/', { name, address: 'E2E Synthetic Way 1', property_type: 'residential' })
  }

  deleteProperty(id: string) {
    return this.call('delete', `/api/properties/${id}`)
  }

  createMeter(propertyId: string, body: Record<string, unknown>) {
    return this.call<{ id: string }>('post', `/api/properties/${propertyId}/meters/`, body)
  }

  addReading(propertyId: string, meterId: string, value: number, readAt: Date, note?: string) {
    return this.call('post', `/api/properties/${propertyId}/meters/${meterId}/readings/`, {
      value, read_at: readAt.toISOString(), note,
    })
  }

  /** Make an existing (seeded or signed-in) user a member of a property. */
  async addMember(propertyId: string, username: string, role: 'user' | 'manager' | 'admin'): Promise<void> {
    const users = await this.call<{ id: string; username: string }[]>('get', '/api/admin/users')
    const u = users.find((x) => x.username === username)
    if (!u) throw new Error(`no such user ${username}`)
    await this.call('post', `/api/properties/${propertyId}/users`, { user_id: u.id, role })
  }

  async users(): Promise<{ id: string; username: string; role: string }[]> {
    return this.call('get', '/api/admin/users')
  }

  /** Oil tank with level readings given as [days ago, litres]. */
  async oilTank(propertyId: string, name: string, levels: [number, number][]): Promise<string> {
    const m = await this.createMeter(propertyId, { meter_type: 'oil', unit: 'L', name, serial_number: 'E2E-TANK-1' })
    for (const [d, l] of levels) await this.addReading(propertyId, m.id, l, daysAgo(d))
    return m.id
  }

  async dispose() {
    await this.ctx.dispose()
  }
}

export const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000)
