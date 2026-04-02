import { useAuthStore } from '@/stores/auth'
import router from '@/router'

/**
 * Drop-in replacement for fetch() that automatically logs the user out
 * and redirects to /login when the server returns 401 Unauthorized.
 */
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, init)
  if (res.status === 401) {
    const auth = useAuthStore()
    auth.logout()
    router.push('/login')
  }
  return res
}
