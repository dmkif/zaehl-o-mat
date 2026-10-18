import { computed, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import { permsFor } from '@/utils/propertyRole'

/** Role of the signed-in user in one property, from GET /api/properties/{id}. No role (error) = read-only UI. */
export function usePropertyPerms(propertyId: string) {
  const authStore = useAuthStore()
  const myRole = ref<string | null>(null)
  const perms = computed(() => permsFor(myRole.value))
  const canWrite = computed(() => perms.value.canWrite)
  const canDelete = computed(() => perms.value.canDelete)

  async function loadRole(): Promise<void> {
    try {
      const res = await apiFetch(`/api/properties/${encodeURIComponent(propertyId)}`, {
        headers: { Authorization: `Bearer ${authStore.token}` },
      })
      myRole.value = res.ok ? ((await res.json())?.my_role ?? null) : null
    } catch {
      myRole.value = null
    }
  }

  return { myRole, canWrite, canDelete, loadRole }
}
