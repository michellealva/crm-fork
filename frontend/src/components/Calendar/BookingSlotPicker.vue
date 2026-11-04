<template>
  <div class="flex items-start">
    <div class="mt-1.5 w-3/12 text-base text-ink-gray-7">
      {{ __('Date & Time') }}
    </div>
    <div class="w-9/12">
      <div
        v-if="slots.loading && !slots.data"
        class="flex h-20 items-center justify-center"
      >
        <LoadingIndicator class="size-5 text-ink-gray-5" />
      </div>
      <div v-else-if="!days.length" class="py-1.5 text-base text-ink-gray-5">
        {{ __('No open times in the next four weeks.') }}
      </div>
      <div v-else class="flex flex-col gap-2">
        <div class="flex gap-1.5 overflow-x-auto pb-1">
          <Button
            v-for="day in days"
            :key="day.key"
            class="shrink-0"
            :variant="day.key === dayKey ? 'solid' : 'subtle'"
            :label="day.label"
            @click="pickDay(day.key)"
          />
        </div>
        <div class="grid grid-cols-4 gap-1.5">
          <Button
            v-for="slot in daySlots"
            :key="slot.start"
            :variant="slot.start === start ? 'solid' : 'subtle'"
            :label="formatTime(slot.start)"
            @click="pickSlot(slot)"
          />
        </div>
        <div class="text-sm text-ink-gray-5">{{ timezone }}</div>
      </div>
    </div>
  </div>
  <div v-if="hostOptions.length > 1" class="flex items-center">
    <div class="w-3/12 text-base text-ink-gray-7">{{ __('Host') }}</div>
    <div class="w-9/12">
      <FormControl
        v-model="host"
        type="select"
        variant="outline"
        :options="hostOptions"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { usersStore } from '@/stores/users'
import {
  Button,
  FormControl,
  LoadingIndicator,
  createResource,
} from 'frappe-ui'
import { computed, ref, watch } from 'vue'

interface HostSlot {
  start: string
  hosts: string[]
  suggested: string
}

const props = defineProps<{ route: string }>()

// the picked slot (UTC ISO) and who hosts it
const start = defineModel<string>('start', { default: '' })
const host = defineModel<string>('host', { default: '' })

const { getUser } = usersStore()
const me = getUser().name
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone

const dayKey = ref('')

const slots = createResource({
  url: 'crm.api.booking.get_host_slots',
  makeParams() {
    const from = new Date()
    const to = new Date(from.getTime() + 28 * 24 * 60 * 60 * 1000)
    return {
      route: props.route,
      start: from.toISOString(),
      end: to.toISOString(),
    }
  },
  auto: true,
  onSuccess() {
    pickDay(days.value[0]?.key || '')
  },
})

watch(
  () => props.route,
  () => {
    start.value = ''
    slots.reload()
  },
)

const dayFormat = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})
const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: 'numeric',
  minute: '2-digit',
})
const keyOf = (iso: string) => new Date(iso).toLocaleDateString('en-CA')
const allSlots = computed(() => (slots.data || []) as HostSlot[])

const days = computed(() => {
  const seen = new Map<string, string>()
  for (const slot of allSlots.value) {
    const key = keyOf(slot.start)
    if (!seen.has(key)) seen.set(key, dayFormat.format(new Date(slot.start)))
  }
  return [...seen].map(([key, label]) => ({ key, label }))
})

const daySlots = computed(() =>
  allSlots.value.filter((s) => keyOf(s.start) === dayKey.value),
)

const hostOptions = computed(() =>
  (allSlots.value.find((s) => s.start === start.value)?.hosts || []).map(
    (user) => ({ label: getUser(user).full_name || user, value: user }),
  ),
)

function formatTime(iso: string) {
  return timeFormat.format(new Date(iso))
}

function pickDay(key: string) {
  dayKey.value = key
  start.value = ''
}

function pickSlot(slot: HostSlot) {
  start.value = slot.start
  // you, when you're free then; otherwise whoever is next in turn
  host.value = slot.hosts.includes(me) ? me : slot.suggested
}

defineExpose({ reload: () => slots.reload() })
</script>
