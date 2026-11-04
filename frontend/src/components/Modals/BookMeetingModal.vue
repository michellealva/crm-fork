<template>
  <Dialog v-model:open="show" :title="__('Book a meeting')">
    <div v-if="pages.loading && !pages.data" class="flex h-40 items-center justify-center">
      <LoadingIndicator class="size-5 text-ink-gray-5" />
    </div>
    <div v-else-if="!pages.data?.length" class="flex flex-col items-center gap-3 py-6 text-center">
      <div class="text-p-base text-ink-gray-6">
        {{ __('There are no live booking pages yet.') }}
      </div>
      <Button :label="__('Set up booking pages')" @click="openSettings" />
    </div>
    <div v-else class="flex flex-col gap-4">
      <FormControl
        v-model="route"
        type="select"
        :label="__('Booking page')"
        :options="pageOptions"
      />

      <div v-if="slots.loading && !slots.data" class="flex h-32 items-center justify-center">
        <LoadingIndicator class="size-5 text-ink-gray-5" />
      </div>
      <div v-else-if="!days.length" class="py-6 text-center text-p-base text-ink-gray-5">
        {{ __('No open times in the next four weeks.') }}
      </div>
      <template v-else>
        <div>
          <div class="mb-1.5 text-sm text-ink-gray-5">{{ __('Day') }}</div>
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
        </div>
        <div>
          <div class="mb-1.5 text-sm text-ink-gray-5">
            {{ __('Time') }} · {{ timezone }}
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
        </div>
        <FormControl
          v-if="hostOptions.length > 1"
          v-model="host"
          type="select"
          :label="__('Host')"
          :options="hostOptions"
        />
      </template>

      <div class="text-p-sm text-ink-gray-5">
        <template v-if="email">
          {{ __('{0} gets an invite with links to reschedule or cancel.', [email]) }}
        </template>
        <template v-else>
          {{ __('Add an email address first, so the invite has somewhere to go.') }}
        </template>
      </div>
      <ErrorMessage v-if="error" :message="error" />
    </div>
    <template v-if="pages.data?.length" #actions>
      <Button
        class="w-full"
        variant="solid"
        :label="__('Book meeting')"
        :disabled="!start || !email"
        :loading="booking"
        @click="bookMeeting"
      />
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { usersStore } from '@/stores/users'
import { showSettings, activeSettingsPage } from '@/composables/settings'
import {
  Button,
  Dialog,
  ErrorMessage,
  FormControl,
  LoadingIndicator,
  call,
  createResource,
  getCachedListResource,
  toast,
} from 'frappe-ui'
import { computed, ref, watch } from 'vue'

interface BookablePage {
  name: string
  title: string
  route: string
  duration: number
  hosts: string[]
}

interface HostSlot {
  start: string
  hosts: string[]
  suggested: string
}

const props = defineProps<{
  doctype: string
  doc: { name: string; email?: string }
}>()

const emit = defineEmits<{ (e: 'booked'): void }>()
const show = defineModel<boolean>({ default: false })

const { getUser } = usersStore()
const me = getUser().name

const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
const email = computed(() => props.doc.email)

const route = ref('')
const dayKey = ref('')
const start = ref('')
const host = ref('')
const booking = ref(false)
const error = ref('')

const pages = createResource({
  url: 'crm.api.booking.pages_to_book',
  auto: true,
  onSuccess(data: BookablePage[]) {
    route.value = data[0]?.route || ''
  },
})

const pageOptions = computed(() =>
  (pages.data || []).map((p: BookablePage) => ({
    label: `${p.title} · ${__('{0} min', [p.duration])}`,
    value: p.route,
  })),
)

const slots = createResource({
  url: 'crm.api.booking.get_host_slots',
  makeParams() {
    const from = new Date()
    const to = new Date(from.getTime() + 28 * 24 * 60 * 60 * 1000)
    return { route: route.value, start: from.toISOString(), end: to.toISOString() }
  },
  onSuccess() {
    pickDay(days.value[0]?.key || '')
  },
})

watch(route, (value) => {
  start.value = ''
  error.value = ''
  if (value) slots.reload()
})

const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const keyOf = (iso: string) => new Date(iso).toLocaleDateString('en-CA')

const days = computed(() => {
  const seen = new Map<string, string>()
  for (const slot of (slots.data || []) as HostSlot[]) {
    const key = keyOf(slot.start)
    if (!seen.has(key)) seen.set(key, dayFormat.format(new Date(slot.start)))
  }
  return [...seen].map(([key, label]) => ({ key, label }))
})

const daySlots = computed(() =>
  ((slots.data || []) as HostSlot[]).filter((s) => keyOf(s.start) === dayKey.value),
)

const selectedSlot = computed(() =>
  ((slots.data || []) as HostSlot[]).find((s) => s.start === start.value),
)

const hostOptions = computed(() =>
  (selectedSlot.value?.hosts || []).map((user) => ({
    label: getUser(user).full_name || user,
    value: user,
  })),
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
  error.value = ''
}

async function bookMeeting() {
  booking.value = true
  error.value = ''
  try {
    await call('crm.api.booking.book_for_record', {
      route: route.value,
      start: start.value,
      doctype: props.doctype,
      docname: props.doc.name,
      host: host.value,
    })
    getCachedListResource(['calendar-events', props.doc.name])?.reload()
    toast.success(__('Meeting booked'))
    emit('booked')
    show.value = false
  } catch (err) {
    const e = err as { messages?: string[]; message?: string; exc_type?: string }
    error.value = e?.messages?.[0] || e?.message || __('Could not book the meeting')
    // the time went to someone else; show what's left
    if (e?.exc_type === 'SlotUnavailableError') {
      start.value = ''
      slots.reload()
    }
  } finally {
    booking.value = false
  }
}

function openSettings() {
  show.value = false
  activeSettingsPage.value = 'Booking Pages'
  showSettings.value = true
}
</script>
