<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { showToast } from 'vant';
import { api, clearToken, currentToken, setToken } from './api';

type UpgradeResult = { id: number; redeem_code: string; prize_name: string; amount: string; status: string; expires_at?: string };
type RedemptionHistory = { id: number; code_no: string; prize_name: string; amount: string; owner_dealer_name: string; redemption_note: string; redeemed_at: string };

const token = ref(currentToken());
const username = ref(''); const password = ref(''); const dealerName = ref('');
const upgradeCode = ref(''); const upgradeResult = ref<UpgradeResult | null>(null); const upgradeError = ref(''); const history = ref<RedemptionHistory[]>([]);
const authed = computed(() => Boolean(token.value));

async function load() { const [profile, items] = await Promise.all([api<{ dealer_name: string }>('/api/dealer/profile'), api<RedemptionHistory[]>('/api/dealer/redemptions')]); dealerName.value = profile.dealer_name; history.value = items; }
async function login() { try { const data = await api<{ access_token: string }>('/api/dealer/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) }); setToken(data.access_token); token.value = data.access_token; await load(); } catch (error) { showToast(error instanceof Error ? error.message : '登录失败'); } }
async function lookupUpgrade() { if (!upgradeCode.value.trim()) return showToast('请输入换购码'); try { upgradeResult.value = await api<UpgradeResult>('/api/dealer/upgrade-orders/lookup', { method: 'POST', body: JSON.stringify({ redeem_code: upgradeCode.value.trim() }) }); upgradeError.value = ''; } catch (error) { upgradeResult.value = null; upgradeError.value = error instanceof Error ? error.message : '查询失败'; } }
async function redeemUpgrade() { if (!upgradeResult.value || upgradeResult.value.status !== 'pending_store_redemption') return; try { const data = await api<UpgradeResult & { message?: string }>('/api/dealer/upgrade-orders/redeem', { method: 'POST', body: JSON.stringify({ redeem_code: upgradeCode.value.trim(), payment_method: 'manual' }) }); upgradeResult.value = data; await load(); showToast(data.message || '核销成功'); } catch (error) { upgradeError.value = error instanceof Error ? error.message : '核销失败'; } }
function logout() { clearToken(); token.value = ''; }
onMounted(() => { if (token.value) load().catch(logout); });
</script>

<template>
  <main class="shell">
    <section v-if="!authed" class="login"><b>槟郎经销商</b><h1>兑换管理</h1><van-cell-group inset><van-field v-model="username" label="账号" placeholder="经销商账号"/><van-field v-model="password" type="password" label="密码" placeholder="登录密码"/></van-cell-group><van-button type="primary" block @click="login">登录</van-button></section>
    <template v-else>
      <header><div><b>{{ dealerName }}</b><small>经销商工作台</small></div><button @click="logout">退出</button></header>
      <section class="panel"><h2>换购核销</h2><van-field v-model="upgradeCode" label="换购码" placeholder="输入用户展示的换购码"/><van-button type="primary" block @click="lookupUpgrade">查询换购订单</van-button><van-cell-group v-if="upgradeResult" inset title="换购订单"><van-cell title="换购码" :value="upgradeResult.redeem_code"/><van-cell title="产品" :value="upgradeResult.prize_name"/><van-cell title="补差价" :value="`¥${Number(upgradeResult.amount).toFixed(2)}`"/><van-cell title="状态" :value="upgradeResult.status === 'pending_store_redemption' ? '待核销' : '已核销'"/><van-button v-if="upgradeResult.status === 'pending_store_redemption'" type="primary" block @click="redeemUpgrade">确认收款并核销</van-button></van-cell-group><p v-if="upgradeError" class="redemption-error">{{ upgradeError }}</p></section>
      <section class="panel"><h2>换购核销记录</h2><van-empty v-if="!history.length" description="暂无换购核销记录"/><article v-for="item in history" :key="item.id"><b>{{ item.prize_name }}</b><p>抽奖号码：{{ item.code_no }} · 所属：{{ item.owner_dealer_name }}</p><p v-if="Number(item.amount)">补差价：¥{{ Number(item.amount).toFixed(2) }}</p><p v-if="item.redemption_note">备注：{{ item.redemption_note }}</p><small>{{ new Date(item.redeemed_at).toLocaleString('zh-CN') }}</small></article></section>
    </template>
  </main>
</template>
