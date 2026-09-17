<script setup lang="ts">
import type { ActivityPrize } from '../types';

defineProps<{ prizes: ActivityPrize[] }>();
const prizeType = (item: ActivityPrize) => item.type.toLowerCase();
</script>

<template>
    <section v-if="prizes.length" class="prize-card">
      <div class="rule-title"><span>本期奖品</span><i></i></div>
      <article v-for="item in prizes" :key="item.id" class="activity-prize"><span>{{ prizeType(item) === 'cash' ? '￥' : '礼' }}</span><div><b>{{ item.name }}</b></div><strong v-if="prizeType(item) === 'cash'">¥ {{ Number(item.cash_amount).toFixed(2) }}</strong><strong v-else-if="prizeType(item) === 'upgrade'">加价 ¥ {{ Number(item.upgrade_price || 0).toFixed(2) }}</strong></article>
    </section>
</template>
