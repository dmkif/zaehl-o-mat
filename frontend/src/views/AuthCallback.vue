<template>
  <div class="flex items-center justify-center min-h-[60vh]">
    <p class="text-gray-500 dark:text-gray-400">{{ $t('auth.processing') }}</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import { extractLoginCode } from '@/utils/authCode'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

onMounted(async () => {
  const code = extractLoginCode(route.hash)
  // Drop the code from the address bar/history right away (it is single use anyway).
  await router.replace({ path: route.path })
  if (!code) {
    router.replace('/login')
    return
  }
  try {
    const res = await apiFetch('/api/auth/exchange', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
    if (!res.ok) throw new Error('exchange failed')
    const data = await res.json()
    authStore.setToken(data.access_token)
    await authStore.fetchMe()
    router.replace('/dashboard')
  } catch {
    router.replace('/login')
  }
})
</script>
