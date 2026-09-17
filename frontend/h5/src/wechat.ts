import { consumerFetch } from './auth';
import { errorMessage, responseBody } from './response';
import type { CashClaim, WechatJsConfig } from './types';

type WechatWindow = Window & {
  wx?: {
    ready?: (callback: () => void) => void;
    config?: (config: WechatJsConfig & { debug: boolean; jsApiList: string[] }) => void;
    checkJsApi?: (options: { jsApiList: string[]; success: () => void; fail: () => void }) => void;
  };
  WeixinJSBridge?: {
    invoke: (name: string, parameters: { mchId: string; appId: string; package: string }, callback: (result?: { err_msg?: string; msg?: string }) => void) => void;
  };
};

let jsConfigCache: { url: string; config: WechatJsConfig } | null = null;
let wxConfiguredForUrl = '';

export async function ensureWechatJsConfig(pageUrl: string): Promise<WechatJsConfig> {
  if (jsConfigCache?.url === pageUrl) return jsConfigCache.config;
  const configResponse = await consumerFetch(`/api/h5/wechat/js-config?url=${encodeURIComponent(pageUrl)}`);
  if (!configResponse.ok) {
    const errorBody = await responseBody<unknown>(configResponse);
    throw new Error(errorMessage(errorBody, '微信确认页配置生成失败'));
  }
  const freshConfig = (await responseBody<WechatJsConfig>(configResponse)) as WechatJsConfig;
  jsConfigCache = { url: pageUrl, config: freshConfig };
  return freshConfig;
}

function wechatSdkReady() {
  return new Promise<void>(resolve => {
    const wxApi = (window as unknown as { wx?: { ready?: (callback: () => void) => void } }).wx;
    if (!wxApi || !wxApi.ready) {
      resolve();
      return;
    }
    wxApi.ready(() => resolve());
  });
}

export async function invokeMerchantTransfer(config: WechatJsConfig, claim: CashClaim, pageUrl: string) {
  const win = window as WechatWindow;
  const wxApi = win.wx;
  if (!wxApi) return { ok: false, msg: '请在微信客户端中打开本页面后领取' };
  if (wxApi.config && wxConfiguredForUrl !== pageUrl) {
    wxApi.config({
      debug: false,
      appId: config.appId,
      timestamp: config.timestamp,
      nonceStr: config.nonceStr,
      signature: config.signature,
      jsApiList: ['requestMerchantTransfer'],
    });
    wxConfiguredForUrl = pageUrl;
  }
  await wechatSdkReady();
  const checkJsApi = wxApi.checkJsApi;
  if (checkJsApi) {
    await new Promise<void>(resolve => checkJsApi.call(wxApi, {
      jsApiList: ['requestMerchantTransfer'],
      success: () => resolve(),
      fail: () => resolve(),
    }));
  }
  if (!win.WeixinJSBridge || !win.WeixinJSBridge.invoke) {
    return { ok: false, msg: '当前微信版本过旧，请升级微信后重试' };
  }
  const bridge = win.WeixinJSBridge;
  return new Promise<{ ok: boolean; cancelled?: boolean; msg?: string }>(resolve => {
    bridge.invoke(
      'requestMerchantTransfer',
      { mchId: claim.mch_id, appId: claim.app_id, package: claim.package },
      (bridgeResult: { err_msg?: string; msg?: string } = {}) => {
        const message = bridgeResult.err_msg || bridgeResult.msg || '';
        if (message === 'requestMerchantTransfer:ok') {
          resolve({ ok: true });
        } else if (message.includes('cancel')) {
          resolve({ ok: false, cancelled: true, msg: '已取消收款确认' });
        } else if (message) {
          resolve({ ok: false, msg: message });
        } else {
          resolve({ ok: false, msg: '微信未返回确认结果，请稍后在“我的奖品”中查看' });
        }
      },
    );
  });
}

