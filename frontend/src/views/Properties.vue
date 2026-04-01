<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold">{{ $t('properties.title') }}</h1>
      <button
        v-if="authStore.isAdmin"
        @click="showCreate = true"
        class="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg text-sm font-medium transition"
      >
        + {{ $t('properties.add') }}
      </button>
    </div>

    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <RouterLink
        v-for="p in properties"
        :key="p.id"
        :to="`/properties/${p.id}/meters`"
        class="block p-4 rounded-xl border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm hover:shadow transition"
      >
        <div class="font-semibold text-lg">{{ p.name }}</div>
        <div class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ p.address }}</div>
        <div class="mt-2 inline-block text-xs px-2 py-0.5 rounded-full bg-brand-100 dark:bg-brand-700 text-brand-700 dark:text-brand-100">
          {{ p.property_type }}
        </div>
      </RouterLink>
    </div>

    <!-- Create modal (minimal) -->
    <Teleport to="body">
      <div v-if="showCreate" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-4">{{ $t('properties.add') }}</h2>
          <form @submit.prevent="createProperty" class="flex flex-col gap-3">
            <input v-model="newProp.name" :placeholder="$t('properties.name')" required
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="newProp.address" :placeholder="$t('properties.address')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <select v-model="newProp.property_type"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="residential">Wohngebäude</option>
              <option value="commercial">Gewerbe</option>
              <option value="mixed">Gemischt</option>
            </select>
            <div class="flex gap-2 justify-end mt-2">
              <button type="button" @click="showCreate = false" class="px-4 py-2 rounded-lg border dark:border-gray-600">
                {{ $t('common.cancel') }}
              </button>
              <button type="submit" class="px-4 py-2 bg-brand-600 text-white rounded-lg font-medium">
                {{ $t('common.save') }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const loading = ref(true)
const properties = ref<any[]>([])
const showCreate = ref(false)
const newProp = ref({ name: '', address: '', property_type: 'residential' })

onMounted(fetchProperties)

async function fetchProperties() {
  loading.value = true
  const res = await fetch('/api/properties', {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) properties.value = await res.json()
  loading.value = false
}

async function createProperty() {
  const res = await fetch('/api/properties', {
    method: 'POST',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(newProp.value),
  })
  if (res.ok) {
    showCreate.value = false
    newProp.value = { name: '', address: '', property_type: 'residential' }
    fetchProperties()
  }
}
</script>
