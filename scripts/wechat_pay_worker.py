"""微信商家转账定时 worker。

职责：
1. 把 PENDING 的现金中奖单自动创建成微信商家转账单；
2. 轮询 PROCESSING/PENDING 单据的上游终态并落库（不自动换号）；
3. 对超过确认时限仍 WAIT_USER_CONFIRM 的单据调用微信撤销接口。

资金安全约定（务必遵守）：
- 仅在微信查询确认旧单号不存在或 CANCELLED 时才由用户侧入口换新单号；
  worker 永远不会自动更换商户单号。
- 日志只输出 payment_id 与脱敏商户单号尾号，不输出 openid、完整单号、
  APIv3 Key 或私钥内容。

运行：
    .venv/bin/python scripts/wechat_pay_worker.py            # 常驻
    .venv/bin/python scripts/wechat_pay_worker.py --once     # 单轮（便于 cron）
"""

import argparse
import logging
import sys
import time

from sqlmodel import Session, select

from app.cache import redis_client
from app.core.config import get_settings
from app.db import engine
from app.models import CashPayment, PaymentStatus
from app.services.wechat_pay import (
    WECHAT_STATE_WAIT_USER_CONFIRM,
    WechatPayConfigError,
    cancel_stale_confirmations,
    process_cash_payment,
    wechatpay_ready,
)

logger = logging.getLogger("betel_lottery.wechat_pay_worker")
settings = get_settings()

WORKER_LOCK_KEY = "wechatpay:worker:leader"


def _acquire_leader_lock(timeout_seconds: int) -> bool:
    return bool(redis_client.set(WORKER_LOCK_KEY, "1", nx=True, ex=timeout_seconds))


def run_once() -> None:
    if not wechatpay_ready():
        raise WechatPayConfigError("微信支付尚未配置完整，worker 无法运行")
    if not _acquire_leader_lock(int(settings.wechatpay_worker_poll_seconds) + 30):
        logger.info("another worker instance is active, skip this round")
        return
    try:
        with Session(engine) as session:
            payments = session.exec(
                select(CashPayment)
                .where(
                    CashPayment.status.in_(
                        [PaymentStatus.PENDING, PaymentStatus.PROCESSING]
                    )
                )
                .order_by(CashPayment.id)
                .limit(settings.wechatpay_worker_batch_size)
            ).all()
            stale_payments = session.exec(
                select(CashPayment)
                .where(
                    CashPayment.status == PaymentStatus.PROCESSING,
                    CashPayment.transfer_state == WECHAT_STATE_WAIT_USER_CONFIRM,
                )
                .order_by(CashPayment.id)
                .limit(settings.wechatpay_worker_batch_size)
            ).all()
        for payment in payments:
            try:
                outcome = process_cash_payment(payment.id)
            except WechatPayConfigError as exc:
                logger.warning("payment_id=%s deferred (config): %s", payment.id, exc)
                continue
            result = outcome.get("result")
            if result == "claim":
                logger.info("payment_id=%s waiting for user confirm", payment.id)
            elif result in {"success", "failed"}:
                logger.info("payment_id=%s terminal result=%s", payment.id, result)
            elif outcome.get("skipped"):
                continue
            else:
                logger.debug("payment_id=%s outcome=%s", payment.id, outcome)
        # 超时未确认单据：统一过一遍撤销判定。
        for payment in stale_payments:
            try:
                cancelled = cancel_stale_confirmations(payment.id)
                if cancelled.get("cancelling"):
                    logger.info("payment_id=%s stale confirmation cancel issued", payment.id)
            except Exception:
                logger.exception("payment_id=%s stale cancel failed", payment.id)
    finally:
        redis_client.delete(WORKER_LOCK_KEY)


def main() -> None:
    parser = argparse.ArgumentParser(description="微信商家转账定时 worker")
    parser.add_argument("--once", action="store_true", help="只运行一轮后退出")
    parser.add_argument("--max-seconds", type=int, default=0, help="常驻模式最多运行秒数（0 表示不限）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    started = time.time()
    while True:
        try:
            run_once()
        except WechatPayConfigError as exc:
            logger.warning("worker paused: %s", exc)
            time.sleep(max(10.0, settings.wechatpay_worker_poll_seconds))
        except Exception:
            logger.exception("worker round failed")
            time.sleep(max(5.0, settings.wechatpay_worker_poll_seconds))
        if args.once:
            break
        if args.max_seconds and time.time() - started >= args.max_seconds:
            logger.info("worker reached max run time, exiting")
            break
        time.sleep(settings.wechatpay_worker_poll_seconds)


if __name__ == "__main__":
    main()
