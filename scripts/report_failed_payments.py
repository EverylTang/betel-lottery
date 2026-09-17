"""诊断微信商家转账失败单（只读，不再自动 requeue）。

新版商家转账的资金安全约定：微信 FAIL 是终态、必须人工处理；CANCELLED 只
允许“对应的用户本人在 H5 我的奖品里点击重新领取”时换新商户单号，任何后台
脚本或定时 worker 都不得自动把 FAILED 重新置为 PENDING，否则可能对同一用户
重复发起或复用已被微信记账的商户单号。

用法：
    .venv/bin/python scripts/report_failed_payments.py [--csv output.csv]
"""

import argparse
import csv

from sqlmodel import Session, select

from app.db import engine
from app.models import CashPayment, PaymentStatus


parser = argparse.ArgumentParser(description="只读展示微信商家转账失败/已撤销现金单")
parser.add_argument("--csv", help="可选的输出 CSV 路径")
args = parser.parse_args()

with Session(engine) as session:
    rows = session.exec(
        select(CashPayment)
        .where(CashPayment.status == PaymentStatus.FAILED)
        .order_by(CashPayment.id)
    ).all()

print(f"failed_payments={len(rows)}")
for payment in rows:
    is_cancelled = payment.transfer_state == "CANCELLED"
    action = "用户可在 H5 重新领取" if is_cancelled else "需人工处理（终态 FAIL）"
    print(payment.id, payment.merchant_order_no, f"amount={payment.amount}", f"state={payment.transfer_state or '-'}", action)

if args.csv and rows:
    with open(args.csv, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["payment_id", "merchant_order_no", "amount", "transfer_state", "fail_reason", "attempts", "建议动作"])
        for payment in rows:
            is_cancelled = payment.transfer_state == "CANCELLED"
            writer.writerow([
                payment.id,
                payment.merchant_order_no,
                str(payment.amount),
                payment.transfer_state or "",
                payment.fail_reason or "",
                payment.attempts,
                "用户可在 H5 重新领取" if is_cancelled else "需人工处理（终态 FAIL）",
            ])
    print(f"wrote_csv={args.csv}")
