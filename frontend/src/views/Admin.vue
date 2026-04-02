<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">{{ $t('admin.title') }}</h1>

    <!-- Users table -->
    <div class="bg-white dark:bg-gray-800 rounded-2xl shadow p-4 mb-6">
      <h2 class="font-semibold mb-3">{{ $t('admin.users') }}</h2>
      <div v-if="loadingUsers" class="text-gray-500 text-sm">{{ $t('common.loading') }}</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
            <th class="text-left py-2">{{ $t('admin.username') }}</th>
            <th class="text-left py-2">{{ $t('admin.email') }}</th>
            <th class="text-center py-2">{{ $t('admin.role') }}</th>
            <th class="py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" class="border-b dark:border-gray-700/50">
            <td class="py-2">{{ u.username }}</td>
            <td class="py-2 text-gray-500">{{ u.email }}</td>
            <td class="py-2 text-center">
              <select
                v-model="u.role"
                @change="updateRole(u)"
                class="text-xs border rounded px-1 py-0.5 dark:bg-gray-700 dark:border-gray-600"
              >
                <option value="user">user</option>
                <option value="manager">manager</option>
                <option value="admin">admin</option>
                <option v-if="authStore.user?.role === 'superadmin'" value="superadmin">superadmin</option>
              </select>
            </td>
            <td class="py-2 text-right">
              <button
                v-if="u.role !== 'superadmin'"
                @click="deleteUser(u)"
                class="text-red-500 hover:text-red-700 text-xs"
              >
                {{ $t('common.delete') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'

const authStore = useAuthStore()
const loadingUsers = ref(true)
const users = ref<any[]>([])

onMounted(fetchUsers)

async function fetchUsers() {
  loadingUsers.value = true
  const res = await apiFetch('/api/admin/users', {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) users.value = await res.json()
  loadingUsers.value = false
}

async function updateRole(u: any) {
  await apiFetch(`/api/admin/users/${u.id}/role`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ role: u.role }),
  })
}

async function deleteUser(u: any) {
  if (!confirm(`Nutzer "${u.username}" wirklich löschen?`)) return
  const res = await apiFetch(`/api/admin/users/${u.id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) users.value = users.value.filter((x) => x.id !== u.id)
}
</script>
