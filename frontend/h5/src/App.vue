<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { showToast } from 'vant';
import { consumerFetch, login, token } from './auth';
import { errorMessage, responseBody } from './response';
import { ensureWechatJsConfig, invokeMerchantTransfer } from './wechat';
import type { ActivityDetail, CashClaim, PrizeResult, ScanResult } from './types';
import ActivityPrizes from './components/ActivityPrizes.vue';

const prizeOpen = ref(false);
const historyOpen = ref(false);
const infoOpen = ref<'rules' | 'service' | 'privacy' | ''>('');
const drawing = ref(false);
const validating = ref(false);
const hasDrawn = ref(false);
const drawTicket = ref('');
const verificationTicket = ref('');
const redemptionCode = ref('');
const verifyingRedemptionCode = ref(false);
const clientLocation = ref('');
const records = ref<PrizeResult[]>([]);
const result = ref<PrizeResult>({ prize_name: '0.30 元现金红包', amount: '0.30', prize_type: 'cash' });
const claimingRecordId = ref<number | null>(null);
const activity = ref<ActivityDetail>({ name: '扫码抽奖活动', description: '', rules: '', prizes: [] });
const wheelRotation = ref(0);
const wheelTransition = ref(false);
const scanState = ref<'idle' | 'loading' | 'redemption' | 'draw' | 'drawn' | 'error'>('idle');
let scanRequestVersion = 0;
const rawCode = computed(() => new URLSearchParams(location.search).get('t'));
const canDraw = computed(() => scanState.value === 'draw' && Boolean(drawTicket.value) && !drawing.value);
const requiresRedemptionCode = computed(() => scanState.value === 'redemption');
const prizeType = (item: { type?: string; prize_type?: string }) => (item.prize_type || item.type || '').toLowerCase();
const resultType = computed(() => prizeType(result.value));
const isWinning = computed(() => resultType.value !== 'none');
const drawLabel = computed(() => drawing.value ? '正在开奖' : '立即抽奖');
const wheelPrizes = computed(() => activity.value.prizes.slice(0, 8));
const wheelBackground = computed(() => {
  const colors = ['#ffe8a5 0 50%', '#e94b2b 50% 100%'];
  const angle = 360 / wheelPrizes.value.length;
  return `conic-gradient(${wheelPrizes.value.map((_, index) => `${colors[index % 2].split(' ')[0]} ${index * angle}deg ${(index + 1) * angle}deg`).join(', ')})`;
});
async function loadRecords() {
  if (!token.value) return;
  const response = await consumerFetch('/api/h5/lottery/records');
  if (response.ok) {
    const data = await responseBody<PrizeResult[] | { items: PrizeResult[] }>(response);
    records.value = Array.isArray(data) ? data : data.items;
  }
}

async function loadActivity(activityId: number, lotteryPolicyId?: number | null) {
  const suffix = lotteryPolicyId ? `?lottery_policy_id=${lotteryPolicyId}` : '';
  const response = await consumerFetch(`/api/h5/activities/${activityId}${suffix}`);
  if (!response.ok) return;
  const data = await responseBody<ActivityDetail>(response);
  if (data.id) activity.value = data;
}

const ruleItems = computed(() => activity.value.rules.split(/\n+/).map(item => item.trim()).filter(Boolean));

function captureClientLocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => { clientLocation.value = `${coords.latitude.toFixed(6)},${coords.longitude.toFixed(6)}`; },
    () => undefined,
    { enableHighAccuracy: false, timeout: 6000, maximumAge: 300000 },
  );
}

async function scan() {
  if (validating.value || drawing.value) return;
  const requestVersion = ++scanRequestVersion;
  validating.value = true;
  scanState.value = rawCode.value ? 'loading' : 'idle';
  try {
    if (!token.value) await login();
    if (!rawCode.value) {
      drawTicket.value = '';
      scanState.value = 'idle';
      return;
    }
    const response = await consumerFetch('/api/h5/scan/validate', { method: 'POST', body: JSON.stringify({ token: rawCode.value }) });
    const data = await responseBody<ScanResult>(response);
    if (requestVersion !== scanRequestVersion) return;
    if (!response.ok) {
      const detail = (data as { detail?: { activity_id?: number; lottery_policy_id?: number | null } }).detail;
      if (detail && typeof detail === 'object' && detail.activity_id) {
        await loadActivity(detail.activity_id, detail.lottery_policy_id);
      }
      throw new Error(errorMessage(data, '二维码不可用'));
    }
    drawTicket.value = data.draw_ticket;
    verificationTicket.value = data.verification_ticket || '';
    redemptionCode.value = '';
    hasDrawn.value = false;
    scanState.value = data.requires_redemption_code ? 'redemption' : 'draw';
    await loadActivity(data.activity_id, data.lottery_policy_id);
  } catch (error) {
    if (requestVersion !== scanRequestVersion) return;
    drawTicket.value = '';
    verificationTicket.value = '';
    hasDrawn.value = error instanceof Error && error.message.includes('已抽奖');
    scanState.value = hasDrawn.value ? 'drawn' : 'error';
    showToast(error instanceof Error ? error.message : '暂时无法参与，请稍后重试');
  } finally {
    if (requestVersion === scanRequestVersion) validating.value = false;
  }
}

async function verifyRedemptionCode() {
  if (!verificationTicket.value || !/^\d{4}$/.test(redemptionCode.value)) {
    showToast('请输入袋内 4 位兑奖码');
    return;
  }
  verifyingRedemptionCode.value = true;
  try {
    const response = await consumerFetch('/api/h5/scan/verify-redemption-code', { method: 'POST', body: JSON.stringify({ verification_ticket: verificationTicket.value, redemption_code: redemptionCode.value }) });
    const data = await responseBody<ScanResult>(response);
    if (!response.ok) throw new Error(errorMessage(data, '兑奖码核验失败'));
    drawTicket.value = data.draw_ticket;
    verificationTicket.value = '';
    redemptionCode.value = '';
    scanState.value = 'draw';
    showToast('兑奖码核验成功，请开始抽奖');
  } catch (error) {
    showToast(error instanceof Error ? error.message : '兑奖码核验失败');
  } finally {
    verifyingRedemptionCode.value = false;
  }
}

async function draw() {
  if (drawing.value) return;
  if (scanState.value !== 'draw' || !drawTicket.value) {
    showToast('抽奖资格已失效，请重新扫描包装二维码');
    return;
  }
  // Invalidate any older scan result before this code transitions to drawn.
  scanRequestVersion += 1;
  const ticket = drawTicket.value;
  drawing.value = true;
  drawTicket.value = '';
  try {
    let nextResult: PrizeResult;
    const requestBody = {
      draw_ticket: ticket,
      idempotency_key: crypto.randomUUID(),
      client_location: clientLocation.value || undefined,
    };
    const response = await consumerFetch('/api/h5/lottery/draw', { method: 'POST', body: JSON.stringify(requestBody) });
    const data = await responseBody<PrizeResult>(response);
    if (!response.ok) throw new Error(errorMessage(data, '抽奖失败'));
    nextResult = data;
    drawTicket.value = '';
    hasDrawn.value = true;
    // Keep the draw surface behind the result animation. The completed state is
    // shown only after the customer closes the prize result.
    scanState.value = 'draw';
    await loadRecords();
    result.value = nextResult;
    if (!wheelPrizes.value.length) {
      prizeOpen.value = true;
      return;
    }
    const targetIndex = Math.max(0, wheelPrizes.value.findIndex(item => item.name === nextResult.prize_name && prizeType(item) === prizeType(nextResult)));
    const segmentAngle = 360 / wheelPrizes.value.length;
    wheelTransition.value = false;
    wheelRotation.value %= 360;
    await nextTick();
    requestAnimationFrame(() => {
      wheelTransition.value = true;
      wheelRotation.value += 360 * 6 + 360 - (targetIndex + 0.5) * segmentAngle;
    });
    await new Promise(resolve => window.setTimeout(resolve, 3600));
    prizeOpen.value = true;
  } catch (error) {
    // Keep the ticket only when the request itself failed, so the user can retry.
    if (scanState.value === 'draw') drawTicket.value = ticket;
    showToast(error instanceof Error ? error.message : '抽奖失败，请稍后重试');
  } finally {
    drawing.value = false;
  }
}

function closePrizeResult() {
  prizeOpen.value = false;
  scanState.value = 'drawn';
}

function displayAmount(item: PrizeResult) {
  return Number(item.amount || 0).toFixed(2);
}

function displayDate(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '刚刚';
}

function sleep(milliseconds: number) {
  return new Promise<void>(resolve => window.setTimeout(resolve, milliseconds));
}

function cashClaimable(item: PrizeResult) {
  if (prizeType(item) !== 'cash' || !item.record_id) return false;
  const status = item.payment_status;
  if (status === 'success' || status === 'manual') return false;
  if (status === 'failed') return item.payment_state === 'CANCELLED';
  return status === 'pending' || status === 'processing' || status === null || status === undefined;
}

function cashActionLabel(item: PrizeResult) {
  if (item.payment_status === 'failed' && item.payment_state === 'CANCELLED') return '重新领取';
  if (item.payment_state === 'WAIT_USER_CONFIRM') return '确认收款';
  return '微信领取';
}

function syncResultFromRecords() {
  const recordId = result.value.record_id;
  if (!recordId) return;
  const latest = records.value.find(item => item.record_id === recordId);
  if (latest) Object.assign(result.value, latest);
}

async function claimCashPrize(item: PrizeResult) {
  if (!item.record_id || claimingRecordId.value !== null) return;
  claimingRecordId.value = item.record_id;
  try {
    const claimResponse = await consumerFetch(`/api/h5/lottery/records/${item.record_id}/cash-claim`, { method: 'POST' });
    const claimData = await responseBody<CashClaim>(claimResponse);
    if (!claimResponse.ok) throw new Error(errorMessage(claimData, '领取请求失败'));
    if (claimData.result !== 'claim') {
      showToast(claimData.detail || '当前状态无需领取');
      await loadRecords();
      syncResultFromRecords();
      return;
    }
    const pageUrl = `${location.origin}${location.pathname}${location.search}`;
    const config = await ensureWechatJsConfig(pageUrl);
    const outcome = await invokeMerchantTransfer(config, claimData, pageUrl);
    await loadRecords();
    syncResultFromRecords();
    if (outcome.cancelled) {
      showToast('已取消确认收款，可稍后在“我的奖品”中重新领取');
    } else if (!outcome.ok) {
      showToast(outcome.msg || '微信确认未完成，请稍后重试');
    } else {
      showToast('已在微信提交收款确认，红包到账后状态自动更新');
      await sleep(3000);
      await loadRecords();
      syncResultFromRecords();
    }
  } catch (error) {
    showToast(error instanceof Error ? error.message : '领取失败，请稍后重试');
  } finally {
    claimingRecordId.value = null;
  }
}

function fulfillmentMessage(item: PrizeResult) {
  if (prizeType(item) === 'cash') {
    const status = item.payment_status;
    const upstream = item.payment_state;
    if (status === 'success') return '红包已到账';
    if (status === 'manual') return '该奖励已转人工发放';
    if (status === 'failed') {
      return upstream === 'CANCELLED' ? '发放未完成，可点击重新领取' : '发放失败，请联系客服';
    }
    if (status === 'processing' && upstream === 'WAIT_USER_CONFIRM') return '等待你在微信中确认收款';
    if (status === 'processing') return '自动发放处理中，请稍后查看';
    if (status === 'pending') return '请在微信中确认收款后到账';
    return '待发放，请稍后查看';
  }
  if (prizeType(item) === 'upgrade') return item.upgrade_status === 'redeemed' ? '换购已完成' : item.upgrade_status === 'expired' ? '换购码已过期' : item.upgrade_redeem_code ? `换购码：${item.upgrade_redeem_code}` : '请联系活动指定渠道办理换购';
  return '';
}

onMounted(() => { captureClientLocation(); void scan(); });
</script>

<template>
  <main class="h5-page">
    <section class="topbar">
      <div class="brand"><span>槟</span><b>槟郎开袋有礼</b></div>
      <button class="history-trigger" @click="historyOpen = true">我的奖品<span v-if="records.length">{{ records.length }}</span></button>
    </section>

    <section class="hero-card">
      <div class="hero-grid-lines"></div>
      <div class="hero-topline"><div class="seal">正品 · 一物一码</div><span>扫码验真</span></div>
      <div class="coin coin-a">￥</div><div class="coin coin-b">￥</div>
      <div class="hero-copy"><p>BETEL LOTTERY</p><h1>槟郎开袋有礼</h1><small>扫码验真，抽取专属好礼</small></div>
      <div class="campaign-mark"><b>扫码<br />赢礼</b><small>SCAN & WIN</small></div>
    </section>

    <section class="draw-panel">
      <template v-if="requiresRedemptionCode">
        <div class="verification-screen">
          <b>输入袋内兑奖码</b>
          <span>请输入包装袋内印刷的 4 位数字，验证后即可参与抽奖。</span>
          <input id="redemption-code" v-model="redemptionCode" inputmode="numeric" maxlength="4" autocomplete="one-time-code" placeholder="请输入 4 位兑奖码" @input="redemptionCode = redemptionCode.replace(/\D/g, '').slice(0, 4)" @keyup.enter="verifyRedemptionCode" />
          <button class="draw-button" :disabled="verifyingRedemptionCode || redemptionCode.length !== 4" @click="verifyRedemptionCode"><span>{{ verifyingRedemptionCode ? '正在验证' : '验证兑奖码' }}</span></button>
        </div>
      </template>
      <template v-else-if="scanState === 'draw'">
        <div v-if="wheelPrizes.length" class="wheel-wrap"><i class="wheel-pointer"></i><div :class="['wheel', { spinning: wheelTransition }]" :style="{ transform: `rotate(${wheelRotation}deg)` }"><div class="wheel-face" :style="{ background: wheelBackground }"><span v-for="(item, index) in wheelPrizes" :key="item.id" class="wheel-label" :style="{ transform: `rotate(${(index + .5) * 360 / wheelPrizes.length}deg)` }"><b :style="{ transform: `translate(-50%, -50%) rotate(${-((index + .5) * 360 / wheelPrizes.length)}deg)` }">{{ item.name }}</b></span></div><div class="wheel-hub">抽奖</div></div></div>
        <div v-else class="wheel-loading"><b>正在准备奖池</b><span>请稍候后再开始抽奖</span></div>
        <p class="draw-hint">点击按钮开始抽奖，转盘将揭晓奖品</p>
        <button class="draw-button" :disabled="drawing || !canDraw" @click="draw"><span>{{ drawLabel }}</span></button>
      </template>
      <template v-else-if="scanState === 'drawn'">
        <div class="status-screen"><b>该二维码已抽奖</b><span>每个二维码仅可参与一次，您可以在“我的奖品”查看本次记录。</span><button class="draw-button" @click="historyOpen = true"><span>查看我的奖品</span></button></div>
      </template>
      <template v-else>
        <div class="wheel-loading"><b>{{ scanState === 'loading' ? '正在核验二维码' : scanState === 'error' ? '二维码暂不可参与' : '请扫描包装二维码' }}</b><span>{{ scanState === 'loading' ? '正在加载活动和兑奖资格' : scanState === 'error' ? '请确认二维码状态后重试' : '扫描后先验证袋内兑奖码，再参与抽奖' }}</span></div>
        <button v-if="rawCode" class="verify-button" :disabled="validating" @click="scan">{{ validating ? '正在加载…' : '重新加载' }}</button>
      </template>
    </section>

    <ActivityPrizes :prizes="activity.prizes" />

    <section class="rule-card">
      <div class="rule-title"><span>活动规则</span><i></i></div>
      <ol v-if="ruleItems.length"><li v-for="item in ruleItems" :key="item">{{ item }}</li></ol>
      <ol v-else><li>扫描包装上的活动二维码，核验成功即可参加抽奖。</li><li>每个二维码限参与一次，中奖结果以系统记录为准。</li></ol>
    </section>
    <footer><button @click="infoOpen = 'rules'">活动规则</button><i>·</i><button @click="infoOpen = 'service'">客服中心</button><i>·</i><button @click="infoOpen = 'privacy'">隐私保护</button></footer>

    <van-popup v-model:show="prizeOpen" round class="result-popup" :close-on-click-overlay="false">
      <div :class="['result-card', { miss: !isWinning }]">
        <div class="result-medal">{{ isWinning ? '￥' : '礼' }}</div>
        <p>{{ isWinning ? '恭喜获得' : '感谢参与' }}</p>
        <h2>{{ result.prize_name }}</h2>
        <strong v-if="resultType === 'cash'">¥ {{ displayAmount(result) }}</strong>
        <small>{{ resultType === 'upgrade' && result.upgrade_redeem_code ? `换购码：${result.upgrade_redeem_code}；请在有效期内到指定渠道办理` : resultType === 'cash' ? fulfillmentMessage(result) : '下次再来，更多好礼等你领取' }}</small>
        <van-button v-if="resultType === 'cash' && cashClaimable(result)" type="danger" block :loading="claimingRecordId === result.record_id" @click="claimCashPrize(result)">{{ claimingRecordId === result.record_id ? '正在拉起微信确认' : cashActionLabel(result) }}</van-button>
        <van-button type="primary" block @click="closePrizeResult">收下好礼</van-button>
      </div>
    </van-popup>

    <van-popup v-model:show="historyOpen" position="bottom" round :style="{ minHeight: '52%' }" @open="loadRecords">
      <div class="history-sheet"><div class="sheet-handle"></div><h2>我的奖品</h2><p class="sheet-note">全部抽奖记录</p>
        <div v-if="records.length" class="record-list">
          <article v-for="item in records" :key="item.record_id || item.drawn_at" class="record">
            <span class="record-icon">{{ prizeType(item) === 'cash' ? '￥' : '礼' }}</span>
            <div>
              <b>{{ item.prize_name }}</b>
              <p v-if="prizeType(item) === 'upgrade' && item.upgrade_redeem_code">换购码：<strong class="fulfillment-code">{{ item.upgrade_redeem_code }}</strong></p>
              <p v-else>{{ item.activity_name || '抽奖活动' }}</p>
              <p v-if="fulfillmentMessage(item)">{{ fulfillmentMessage(item) }}</p>
              <p>{{ displayDate(item.drawn_at) }}</p>
            </div>
            <strong v-if="prizeType(item) === 'cash'">¥ {{ displayAmount(item) }}</strong>
            <button v-if="cashClaimable(item)" class="cash-claim-btn" :disabled="claimingRecordId === item.record_id" @click="claimCashPrize(item)">{{ claimingRecordId === item.record_id ? '拉起中' : cashActionLabel(item) }}</button>
          </article>
        </div>
        <div v-else class="empty-record">当前筛选暂无奖品</div>
      </div>
    </van-popup>
    <van-popup :show="Boolean(infoOpen)" round class="info-popup" @update:show="value => { if (!value) infoOpen = '' }">
      <section class="info-sheet"><h2>{{ infoOpen === 'rules' ? '活动规则' : infoOpen === 'service' ? '客服中心' : '隐私保护' }}</h2><template v-if="infoOpen === 'rules'"><ol><li v-for="item in ruleItems" :key="item">{{ item }}</li></ol></template><p v-else-if="infoOpen === 'service'">如需协助，请联系活动包装或品牌官方公布的服务渠道，并提供中奖记录时间与奖品信息以便核验。</p><p v-else>为完成抽奖资格核验、中奖记录和奖品发放，我们会保存账号标识、抽奖记录与请求 IP；仅在您授权后保存本次定位坐标。您可通过官方服务渠道申请查阅、更正或删除个人信息；存在待兑奖奖品时会先完成履约。</p><van-button block type="primary" @click="infoOpen = ''">关闭</van-button></section>
    </van-popup>
  </main>
</template>
