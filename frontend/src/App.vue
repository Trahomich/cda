<script setup lang="ts">
import AppToast from '@/components/AppToast.vue'
import PwaUpdateBanner from '@/components/PwaUpdateBanner.vue'
</script>

<template>
  <PwaUpdateBanner />
  <RouterView />
  <AppToast />
</template>
