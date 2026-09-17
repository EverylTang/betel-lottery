"""微信支付商家转账服务模块。

拆分后的模块：
- signature: 签名验证相关
- transfer: 转账、撤销、查询相关
- notification: 回调处理相关
- payout: 高层业务逻辑（自动打款、领取、超时撤销）

从子模块导出所有公开接口以保持向后兼容。
"""

from app.services.wechat_pay.notification import apply_notification, decrypt_resource
from app.services.wechat_pay.signature import (
    WechatPayConfigError,
    build_authorization,
    verify_signature,
    wechatpay_ready,
)
from app.services.wechat_pay.transfer import (
    TransferBillResult,
    TransferQueryFailed,
    TransferRequestFailed,
    cancel_transfer_bill,
    create_transfer_bill,
    query_transfer_bill,
    WECHAT_STATE_ACCEPTED,
    WECHAT_STATE_CANCELING,
    WECHAT_STATE_CANCELLED,
    WECHAT_STATE_FAIL,
    WECHAT_STATE_PROCESSING,
    WECHAT_STATE_SUCCESS,
    WECHAT_STATE_TRANSFERING,
    WECHAT_STATE_WAIT_USER_CONFIRM,
    WECHAT_TERMINAL_STATES,
)
from app.services.wechat_pay.payout import (
    cancel_stale_confirmations,
    claim_cash_payment,
    ensure_payment_transfer,
    process_cash_payment,
)

__all__ = [
    "WechatPayConfigError",
    "TransferRequestFailed",
    "TransferQueryFailed",
    "TransferBillResult",
    "wechatpay_ready",
    "build_authorization",
    "verify_signature",
    "decrypt_resource",
    "create_transfer_bill",
    "query_transfer_bill",
    "cancel_transfer_bill",
    "apply_notification",
    "ensure_payment_transfer",
    "process_cash_payment",
    "claim_cash_payment",
    "cancel_stale_confirmations",
    "WECHAT_STATE_ACCEPTED",
    "WECHAT_STATE_PROCESSING",
    "WECHAT_STATE_WAIT_USER_CONFIRM",
    "WECHAT_STATE_TRANSFERING",
    "WECHAT_STATE_SUCCESS",
    "WECHAT_STATE_FAIL",
    "WECHAT_STATE_CANCELING",
    "WECHAT_STATE_CANCELLED",
    "WECHAT_TERMINAL_STATES",
]