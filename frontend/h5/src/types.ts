export type PrizeResult = { record_id?: number; prize_name: string; prize_type: string; amount: string; payment_status?: string | null; payment_state?: string | null; payment_fail_reason?: string | null; payment_attempts?: number; payment_id?: number | null; redemption_status?: string | null; drawn_at?: string; code_no?: string; activity_name?: string; upgrade_redeem_code?: string | null; upgrade_amount?: string; upgrade_status?: string | null; upgrade_expires_at?: string | null };
export type ActivityPrize = { id: number; name: string; type: string; cash_amount: string; upgrade_price?: string };
export type ActivityDetail = { id?: number; name: string; description: string; rules: string; prizes: ActivityPrize[] };
export type WechatJsConfig = { appId: string; timestamp: string; nonceStr: string; signature: string };

export type ScanResult = { draw_ticket: string; verification_ticket?: string; requires_redemption_code?: boolean; activity_id: number; lottery_policy_id?: number | null };
export type CashClaim = { result: string; detail?: string; app_id: string; mch_id: string; package: string };
