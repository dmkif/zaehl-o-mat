/** Pure helpers for the oil delivery form (no DOM, no network). */

export interface DeliveryForm {
  delivered_at: string // value of <input type="datetime-local">
  liters: string
  total_price: string
  price_per_liter: string
  supplier: string
  note: string
}

export type FormErrorKey = 'date_required' | 'date_invalid' | 'liters_invalid' | 'total_price_invalid' | 'price_per_liter_invalid'

export type PayloadResult =
  | { ok: true; payload: Record<string, unknown> }
  | { ok: false; error: FormErrorKey }

/** Parse "1.234,5" / "1234.5" / "1234,5" to a number; NaN if not a plain number. */
export function parseDecimal(raw: string): number {
  let s = (raw ?? '').trim().replace(/\s/g, '')
  if (!s) return NaN
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.')
  const parts = s.split('.')
  if (parts.length > 2 || parts.some((x) => x === '' || !/^\d+$/.test(x))) return NaN
  return Number(s)
}

/** API decimal ("1500.00", 87.50) -> text for an input: no trailing zeros, '.' separator (parseDecimal reads it back). */
export function formatInputNumber(v: number | string | null | undefined): string {
  if (v == null || v === '') return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  return String(n)
}

/** Build the API body from the form; validates client-side, server stays authoritative. */
export function buildDeliveryPayload(f: DeliveryForm): PayloadResult {
  if (!f.delivered_at) return { ok: false, error: 'date_required' }
  const d = new Date(f.delivered_at)
  if (Number.isNaN(d.getTime())) return { ok: false, error: 'date_invalid' }
  const liters = parseDecimal(f.liters)
  if (!(liters > 0)) return { ok: false, error: 'liters_invalid' }
  const payload: Record<string, unknown> = {
    delivered_at: d.toISOString(),
    liters,
    total_price: null,
    price_per_liter: null,
    supplier: f.supplier.trim() || null,
    note: f.note.trim() || null,
  }
  if (f.total_price.trim()) {
    const v = parseDecimal(f.total_price)
    if (!(v >= 0)) return { ok: false, error: 'total_price_invalid' }
    payload.total_price = v
  }
  if (f.price_per_liter.trim()) {
    const v = parseDecimal(f.price_per_liter)
    if (!(v >= 0)) return { ok: false, error: 'price_per_liter_invalid' }
    payload.price_per_liter = v
  }
  return { ok: true, payload }
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** ISO timestamp -> local "YYYY-MM-DDTHH:mm" for datetime-local; '' if unparsable. */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Make a FastAPI/Pydantic 422 `detail` (string | list | object) readable. Never "[object Object]". */
export function formatApiError(detail: unknown, fallback: string): string {
  if (detail == null || detail === '') return fallback
  if (typeof detail === 'string') return detail
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail)
  if (Array.isArray(detail)) {
    const parts = detail.map((x) => formatApiError(x, '')).filter(Boolean)
    return parts.length ? parts.join('; ') : fallback
  }
  if (typeof detail === 'object') {
    const o = detail as Record<string, unknown>
    const msg = o.msg ?? o.message ?? o.detail
    if (msg != null) {
      const text = formatApiError(msg, '')
      const loc = Array.isArray(o.loc) ? o.loc.filter((l) => l !== 'body').join('.') : ''
      if (text) return loc ? `${loc}: ${text}` : text
    }
    try {
      return JSON.stringify(detail)
    } catch {
      return fallback
    }
  }
  return fallback
}
