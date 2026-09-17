from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import Activity


def _beijing_date(value: datetime) -> str:
    utc_value = value.replace(tzinfo=timezone.utc)
    return utc_value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日")


def generate_activity_rules(activity: Activity) -> str:
    """Return the standard H5 rules with the current activity limits inserted."""
    return "\n".join(
        [
            f"活动时间：{_beijing_date(activity.start_at)}至{_beijing_date(activity.end_at)}（北京时间）。",
            "扫描包装内活动二维码，核验通过后即可参与；每个二维码仅限抽奖一次。",
            "中奖结果以系统记录为准，奖品将展示在“我的奖品”中。",
            "现金奖请在微信内确认收款，到账状态以微信支付结果为准。",
            "换购奖请向所属经销商出示换购码，现场付款并核销。",
            "如发现异常参与行为，平台有权取消活动资格并保留最终解释权。",
        ]
    )
