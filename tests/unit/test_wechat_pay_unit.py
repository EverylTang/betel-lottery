from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock
import sys
from pathlib import Path

import pytest
from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import (
    CashPayment,
    LotteryRecord,
    PaymentStatus,
    RedemptionStatus,
)
from app.services.wechat_pay import payout as wechat_pay_module

WECHAT_STATE_SUCCESS = wechat_pay_module.WECHAT_STATE_SUCCESS
WECHAT_STATE_FAIL = wechat_pay_module.WECHAT_STATE_FAIL
WECHAT_STATE_CANCELLED = wechat_pay_module.WECHAT_STATE_CANCELLED
WECHAT_STATE_PROCESSING = wechat_pay_module.WECHAT_STATE_PROCESSING
WECHAT_STATE_WAIT_USER_CONFIRM = wechat_pay_module.WECHAT_STATE_WAIT_USER_CONFIRM
TransferBillResult = wechat_pay_module.TransferBillResult
_apply_upstream_result = wechat_pay_module._apply_upstream_result
_clear_provider_bill_slots = wechat_pay_module._clear_provider_bill_slots
_reset_for_reclaim = wechat_pay_module._reset_for_reclaim
_mark_failed = wechat_pay_module._mark_failed


class TestClearProviderBillSlots:

    def test_clear_provider_bill_slots_clears_all_fields(self):
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
            provider_out_bill_no="CASH000000000100",
            transfer_bill_no="WX123456",
            transfer_state=WECHAT_STATE_PROCESSING,
            package_info="package_data",
            notify_event_id="event123",
            fail_reason="some reason",
        )

        _clear_provider_bill_slots(payment)

        assert payment.provider_out_bill_no is None
        assert payment.transfer_bill_no is None
        assert payment.transfer_state is None
        assert payment.package_info is None
        assert payment.notify_event_id is None
        assert payment.fail_reason is None


class TestResetForReclaim:

    def test_reset_for_reclaim_resets_payment_to_pending(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.FAILED,
            transfer_state=WECHAT_STATE_CANCELLED,
            provider_out_bill_no="CASH000000000100",
            fail_reason="cancelled",
        )

        _reset_for_reclaim(session, payment, "用户重新领取")

        assert payment.status == PaymentStatus.PENDING
        assert payment.provider == "wechat_mch_transfer"
        assert payment.processed_at is None
        assert payment.provider_error_note == "用户重新领取"
        assert payment.provider_out_bill_no is None
        assert payment.transfer_state is None
        session.add.assert_called_once_with(payment)
        session.commit.assert_called_once()


class TestApplyUpstreamResult:

    def test_apply_upstream_result_success_state(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_SUCCESS,
            transfer_bill_no="WX123456",
            package_info="package_data",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.SUCCESS
        assert payment.provider == "wechat_mch_transfer"
        assert payment.provider_out_bill_no == "CASH000000000100"
        assert payment.transfer_bill_no == "WX123456"
        assert payment.transfer_state == WECHAT_STATE_SUCCESS
        assert payment.package_info == "package_data"
        assert payment.fail_reason is None
        assert payment.processed_at is not None
        assert record.redemption_status == RedemptionStatus.REDEEMED
        assert record.redemption_note == "微信商家转账成功，已到账"
        session.add.assert_called()
        session.commit.assert_called_once()

    def test_apply_upstream_result_fail_state(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_FAIL,
            transfer_bill_no="WX123456",
            fail_reason="余额不足",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.FAILED
        assert payment.transfer_state == WECHAT_STATE_FAIL
        assert payment.fail_reason == "余额不足"
        assert payment.processed_at is not None
        assert record.redemption_status == RedemptionStatus.PENDING
        assert "余额不足" in record.redemption_note
        session.commit.assert_called_once()

    def test_apply_upstream_result_cancelled_state(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_CANCELLED,
            transfer_bill_no="WX123456",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.FAILED
        assert payment.transfer_state == WECHAT_STATE_CANCELLED
        assert payment.processed_at is not None
        assert record.redemption_status == RedemptionStatus.PENDING
        assert "撤销" in record.redemption_note
        assert "重新领取" in record.redemption_note
        session.commit.assert_called_once()

    def test_apply_upstream_result_processing_state(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_PROCESSING,
            transfer_bill_no="WX123456",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.PROCESSING
        assert payment.transfer_state == WECHAT_STATE_PROCESSING
        assert payment.processed_at is None
        session.commit.assert_called_once()

    def test_apply_upstream_result_wait_user_confirm_state(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_WAIT_USER_CONFIRM,
            transfer_bill_no="WX123456",
            package_info="package_for_confirm",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.PROCESSING
        assert payment.transfer_state == WECHAT_STATE_WAIT_USER_CONFIRM
        assert payment.package_info == "package_for_confirm"
        session.commit.assert_called_once()

    def test_apply_upstream_result_no_record_still_updates_payment(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
        )
        session.get.return_value = None

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_SUCCESS,
            transfer_bill_no="WX123456",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.SUCCESS
        assert payment.transfer_state == WECHAT_STATE_SUCCESS
        session.commit.assert_called_once()


class TestMarkFailed:

    def test_mark_failed_sets_failed_status(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        _mark_failed(session, payment, "配置错误", "缺少 OpenID")

        assert payment.status == PaymentStatus.FAILED
        assert payment.fail_reason == "配置错误"
        assert payment.provider_error_note == "缺少 OpenID"
        assert payment.processed_at is not None
        assert payment.provider_updated_at is not None
        assert record.redemption_status == RedemptionStatus.PENDING
        assert "配置错误" in record.redemption_note
        session.add.assert_called()
        session.commit.assert_called_once()

    def test_mark_failed_truncates_long_reason(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
        )
        record = Mock(spec=LotteryRecord)
        record.id = 100
        session.get.return_value = record

        long_reason = "X" * 300
        long_note = "Y" * 600

        _mark_failed(session, payment, long_reason, long_note)

        assert len(payment.fail_reason) <= 256
        assert len(payment.provider_error_note) <= 512
        session.commit.assert_called_once()

    def test_mark_failed_handles_missing_record(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
        )
        session.get.return_value = None

        _mark_failed(session, payment, "记录缺失", None)

        assert payment.status == PaymentStatus.FAILED
        assert payment.fail_reason == "记录缺失"
        session.commit.assert_called_once()


class TestPaymentStateTransitions:

    def test_state_transition_pending_to_processing(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
        )
        session.get.return_value = None

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_PROCESSING,
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.PROCESSING

    def test_state_transition_processing_to_success(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
        )
        session.get.return_value = None

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_SUCCESS,
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.SUCCESS
        assert payment.processed_at is not None

    def test_state_transition_processing_to_failed(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.PROCESSING,
        )
        session.get.return_value = None

        result = TransferBillResult(
            out_bill_no="CASH000000000100",
            state=WECHAT_STATE_FAIL,
            fail_reason="系统错误",
        )

        _apply_upstream_result(session, payment, result)

        assert payment.status == PaymentStatus.FAILED
        assert payment.fail_reason == "系统错误"

    def test_state_transition_failed_to_pending_on_reclaim(self):
        session = Mock(spec=Session)
        payment = CashPayment(
            id=1,
            lottery_record_id=100,
            merchant_order_no="CASH000000000100",
            amount=Decimal("10.00"),
            status=PaymentStatus.FAILED,
            transfer_state=WECHAT_STATE_CANCELLED,
        )

        _reset_for_reclaim(session, payment, "用户重新领取")

        assert payment.status == PaymentStatus.PENDING
