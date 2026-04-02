import { useAuthStore } from '@/stores/auth'
import router from '@/router'

/**
 * Drop-in replacement for fetch() that automatically logs the user out
 * and redirects to /login when the server returns 401 Unauthorized.
 * Also wraps network-level errors (TypeError) with a user-friendly message.
 */
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(input, init)
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error('Network error — please check your connection.')
    }
    throw err
  }
  if (res.status === 401) {
    const auth = useAuthStore()
    auth.logout()
    router.push('/login')
  }
  return res
}
