<template>
  <div>
    <div class="flex items-center gap-3 mb-6">
      <RouterLink :to="`/properties`" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 text-sm">
        ← {{ $t('properties.title') }}
      </RouterLink>
      <span class="text-gray-400">/</span>
      <h1 class="text-2xl font-bold">{{ $t('meters.title') }}</h1>
    </div>

    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <RouterLink
        v-for="m in meters"
        :key="m.id"
        :to="`/properties/${propertyId}/meters/${m.id}`"
        class="block p-4 rounded-xl border dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm hover:shadow transition"
      >
        <div class="flex items-center justify-between">
          <span class="font-semibold">{{ m.name }}</span>
          <MeterTypeIcon :type="m.meter_type" />
        </div>
        <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ m.unit }} · {{ m.serial_number }}</div>
        <div class="text-xs text-gray-400 mt-1">{{ m.location }}</div>
      </RouterLink>

      <button
        v-if="authStore.isManager"
        @click="showCreate = true"
        class="p-4 rounded-xl border-2 border-dashed dark:border-gray-600 flex items-center justify-center text-gray-400 hover:text-brand-600 hover:border-brand-400 transition"
      >
        + {{ $t('meters.add') }}
      </button>
    </div>

    <!-- Create meter modal -->
    <Teleport to="body">
      <div v-if="showCreate" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md shadow-xl">
          <h2 class="text-xl font-bold mb-4">{{ $t('meters.add') }}</h2>
          <form @submit.prevent="createMeter" class="flex flex-col gap-3">
            <input v-model="newMeter.name" :placeholder="$t('meters.name')" required
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <select v-model="newMeter.meter_type"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="water">💧 Wasser</option>
              <option value="electricity">⚡ Strom</option>
              <option value="oil">🛢️ Heizöl</option>
            </select>
            <select v-model="newMeter.unit"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2">
              <option value="m³">m³</option>
              <option value="kWh">kWh</option>
              <option value="L">Liter</option>
            </select>
            <input v-model="newMeter.serial_number" :placeholder="$t('meters.serial')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
            <input v-model="newMeter.location" :placeholder="$t('meters.location')"
              class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2" />
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
import { RouterLink, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import MeterTypeIcon from '@/components/MeterTypeIcon.vue'

const authStore = useAuthStore()
const route = useRoute()
const propertyId = route.params.propertyId as string

const loading = ref(true)
const meters = ref<any[]>([])
const showCreate = ref(false)
const newMeter = ref({
  name: '', meter_type: 'water', unit: 'm³',
  serial_number: '', location: '',
})

onMounted(fetchMeters)

async function fetchMeters() {
  loading.value = true
  const res = await fetch(`/api/properties/${propertyId}/meters`, {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) meters.value = await res.json()
  loading.value = false
}

async function createMeter() {
  const res = await fetch(`/api/properties/${propertyId}/meters`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(newMeter.value),
  })
  if (res.ok) {
    showCreate.value = false
    fetchMeters()
  }
}
</script>
