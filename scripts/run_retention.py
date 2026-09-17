"""Preview or execute scheduled anonymization of expired consumer data."""

import argparse
import secrets
from datetime import timedelta

from sqlmodel import Session, select

from app.core.config import get_settings
from app.db import engine
from app.models import Consumer, LotteryRecord, RedemptionStatus, utc_now


def eligible_consumers(session: Session, cutoff):
    consumers = session.exec(select(Consumer).where(Consumer.anonymized_at.is_(None))).all()
    for consumer in consumers:
        records = session.exec(select(LotteryRecord).where(LotteryRecord.consumer_id == consumer.id)).all()
        if records and max(record.created_at for record in records) < cutoff and not any(record.redemption_status == RedemptionStatus.PENDING for record in records):
            yield consumer, records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Apply anonymization; default is preview only")
    args = parser.parse_args()
    settings = get_settings()
    cutoff = utc_now() - timedelta(days=settings.personal_data_retention_days)
    with Session(engine) as session:
        candidates = list(eligible_consumers(session, cutoff))
        print(f"retention_cutoff={cutoff.isoformat()} candidates={len(candidates)}")
        for consumer, records in candidates:
            print(f"consumer_id={consumer.id} record_count={len(records)}")
        if not args.execute:
            return
        for consumer, records in candidates:
            for record in records:
                record.client_ip = None
                record.client_location = None
                session.add(record)
            consumer.openid = f"anonymized-{consumer.id}-{secrets.token_hex(12)}"
            consumer.nickname = None
            consumer.risk_status = "blocked"
            consumer.risk_note = "超过保留期限，已自动匿名化"
            consumer.anonymized_at = utc_now()
            session.add(consumer)
        session.commit()
        print(f"anonymized={len(candidates)}")


if __name__ == "__main__":
    main()
