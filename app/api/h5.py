import hashlib
import json
import logging
import secrets
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlmodel import select

from app.core.config import get_settings
from app.core.http import client_ip_or_unknown, resolve_client_ip

logger = logging.getLogger("betel_lottery.h5")
from app.cache import enforce_rate_limit, redis_client
from app.deps import SessionDep, current_consumer
from app.models import Activity, ActivityPrize, CashPayment, Consumer, LotteryPolicy, LotteryPolicyPrize, LotteryRecord, Prize, PrizeType, QrCode, UpgradeOrder
from app.schemas import DevLoginRequest, DrawRequest, RedemptionCodeVerifyRequest, ScanValidateRequest
from app.security import make_token, parse_token
from app.services.activity_rules import generate_activity_rules
from app.services.lottery import draw, public_record, validate_scan, verify_redemption_code
from app.services.wechat_jsapi import build_jsapi_config
from app.services.wechat_pay import TransferQueryFailed, WechatPayConfigError, claim_cash_payment

router = APIRouter(prefix="/api/h5", tags=["h5"])
settings = get_settings()


def _wechat_enabled() -> bool:
    return bool(settings.wechat_app_id and settings.wechat_app_secret)


def _wechat_oauth_runtime_enabled() -> bool:
    """Enable real OAuth for staging tunnels without weakening development mode."""
    return _wechat_enabled() and settings.environment in {"staging", "production"}


def _oauth_redirect_uri(request: Request) -> str:
    if settings.wechat_oauth_redirect_uri:
        return settings.wechat_oauth_redirect_uri
    return str(request.url_for("wechat_oauth_callback"))


def _safe_oauth_return_path(raw: str | None) -> str:
    """Keep only a same-app path+query so OAuth callbacks cannot open redirect."""
    if not raw:
        return "/"
    if raw.startswith("//") or "\\" in raw or any(ord(char) < 32 for char in raw):
        return "/"
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return "/"
    return parsed.path if not parsed.query else f"{parsed.path}?{parsed.query}"


def _wechat_callback_error(message: str, return_path: str = "/") -> RedirectResponse:
    base = settings.h5_base_url.rstrip("/")
    return RedirectResponse(f"{base}{return_path}#wechat_error={quote(message)}", status_code=303)


@router.get("/auth/mode")
def auth_mode():
    """Expose only the selected login mode; never expose app secrets."""
    return {"oauth_enabled": _wechat_oauth_runtime_enabled()}


@router.get("/auth/wechat/authorize")
def wechat_authorize(request: Request, return_to: str | None = None):
    if not _wechat_enabled():
        raise HTTPException(status_code=503, detail="微信 OAuth 尚未配置公众号 AppID/AppSecret")
    state = make_token(
        secrets.token_urlsafe(24),
        "wechat_oauth_state",
        10,
        return_to=_safe_oauth_return_path(return_to),
    )
    query = urlencode({
        "appid": settings.wechat_app_id,
        "redirect_uri": _oauth_redirect_uri(request),
        "response_type": "code",
        "scope": settings.wechat_oauth_scope,
        "state": state,
    })
    return RedirectResponse(f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect", status_code=307)


@router.get("/auth/wechat/callback", name="wechat_oauth_callback")
def wechat_oauth_callback(request: Request, session: SessionDep, code: str | None = None, state: str | None = None):
    logger.info(f"微信 OAuth 回调开始，code={'[已提供]' if code else '[缺失]'}, state={'[已提供]' if state else '[缺失]'}")
    
    errcode = request.query_params.get("errcode")
    errmsg = request.query_params.get("errmsg") or "微信授权未完成，请重新进入活动"
    if errcode:
        logger.warning(f"微信 OAuth 回调包含错误码：errcode={errcode}, errmsg={errmsg}")
        if state:
            try:
                state_payload = parse_token(state, "wechat_oauth_state")
                return _wechat_callback_error(errmsg, _safe_oauth_return_path(str(state_payload.get("return_to"))))
            except HTTPException as exc:
                logger.warning(f"state token 解析失败（存在 errcode 场景）：{exc}")
                pass
        return _wechat_callback_error(errmsg)
    if not code or not state:
        logger.error(f"微信 OAuth 回调参数不完整：code={bool(code)}, state={bool(state)}")
        if state:
            try:
                state_payload = parse_token(state, "wechat_oauth_state")
                return _wechat_callback_error("微信 OAuth 回调参数不完整", _safe_oauth_return_path(str(state_payload.get("return_to"))))
            except HTTPException as exc:
                logger.warning(f"state token 解析失败（缺少 code 场景）：{exc}")
                pass
        return _wechat_callback_error("微信 OAuth 回调参数不完整")
    try:
        state_payload = parse_token(state, "wechat_oauth_state")
        state_key = f"wechat:oauth-state:{hashlib.sha256(state.encode()).hexdigest()}"
        if not redis_client.set(state_key, "1", nx=True, ex=600):
            raise HTTPException(status_code=400, detail="微信 OAuth state 已使用")
        logger.info(f"state token 解析成功，return_to={state_payload.get('return_to')}")
    except HTTPException as exc:
        logger.error(f"state token 验证失败：{exc}", exc_info=True)
        raise HTTPException(status_code=400, detail="微信 OAuth state 无效或已过期") from exc
    return_path = _safe_oauth_return_path(str(state_payload.get("return_to")))
    if not _wechat_enabled():
        logger.error("微信 OAuth 未配置 AppID/AppSecret")
        return _wechat_callback_error("微信 OAuth 尚未配置", return_path)
    try:
        logger.info(f"开始请求微信 access_token，appid={settings.wechat_app_id[:8]}...")
        with httpx.Client(timeout=settings.wechat_api_timeout_seconds) as client:
            token_response = client.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={"appid": settings.wechat_app_id, "secret": settings.wechat_app_secret, "code": code, "grant_type": "authorization_code"},
            )
            logger.info(f"微信 API 响应状态码：{token_response.status_code}")
            token_response.raise_for_status()
            token_data = token_response.json()
            logger.debug(f"微信 API 响应数据：{json.dumps({k: v for k, v in token_data.items() if k != 'access_token'})}")
    except httpx.TimeoutException as exc:
        logger.error(f"微信 API 请求超时：{exc}", exc_info=True)
        return _wechat_callback_error("微信授权服务响应超时，请稍后重试", return_path)
    except httpx.ConnectError as exc:
        logger.error(f"微信 API 连接失败：{exc}", exc_info=True)
        return _wechat_callback_error("微信授权服务连接失败，请检查网络或稍后重试", return_path)
    except httpx.HTTPStatusError as exc:
        logger.error(f"微信 API HTTP 错误：status_code={exc.response.status_code}, body={exc.response.text[:200]}", exc_info=True)
        return _wechat_callback_error(f"微信授权服务异常（HTTP {exc.response.status_code}），请稍后重试", return_path)
    except httpx.HTTPError as exc:
        logger.error(f"微信 API 网络错误：{exc}", exc_info=True)
        return _wechat_callback_error("微信授权服务暂时不可用，请稍后重试", return_path)
    except ValueError as exc:
        logger.error(f"微信 API 响应解析失败：{exc}", exc_info=True)
        return _wechat_callback_error("微信授权响应格式错误，请稍后重试", return_path)
    if token_data.get("errcode") or not token_data.get("openid"):
        error_code = token_data.get("errcode")
        error_msg = token_data.get("errmsg", "")
        logger.error(f"微信 OAuth 业务错误：errcode={error_code}, errmsg={error_msg}, has_openid={bool(token_data.get('openid'))}")
        detail = f"微信授权未完成（{error_code}），请重新进入活动" if error_code else "微信授权失败，请重新进入活动"
        return _wechat_callback_error(detail, return_path)
    openid = str(token_data["openid"]).strip()
    logger.info(f"获得微信 openid：{openid[:8]}..., 开始查询或创建用户")
    try:
        consumer = session.exec(select(Consumer).where(Consumer.openid == openid)).first()
        if not consumer:
            logger.info(f"新用户，创建 Consumer 记录：openid={openid[:8]}...")
            consumer = Consumer(openid=openid)
            session.add(consumer)
        session.commit()
        session.refresh(consumer)
        logger.info(f"用户处理成功：consumer_id={consumer.id}")
    except Exception as exc:
        logger.error(f"用户数据库操作失败：{exc}", exc_info=True)
        session.rollback()
        return _wechat_callback_error("用户信息保存失败，请稍后重试", return_path)
    access_token = make_token(str(consumer.id), "consumer", 1440)
    logger.info(f"OAuth 流程完成，跳转至：{return_path}")
    return RedirectResponse(f"{settings.h5_base_url.rstrip('/')}{return_path}#wechat_token={access_token}", status_code=303)


@router.post("/auth/dev-login")
def dev_login(payload: DevLoginRequest, request: Request, session: SessionDep):
    if _wechat_oauth_runtime_enabled():
        raise HTTPException(status_code=403, detail="生产环境必须使用微信 OAuth")
    client_ip = client_ip_or_unknown(request)
    enforce_rate_limit(f"rate:h5-dev-login:{client_ip}:{payload.openid}", 20, 300)
    consumer = session.exec(select(Consumer).where(Consumer.openid == payload.openid)).first()
    if not consumer:
        consumer = Consumer(openid=payload.openid, nickname=payload.nickname)
        session.add(consumer)
        session.commit()
        session.refresh(consumer)
    return {"access_token": make_token(str(consumer.id), "consumer", 1440), "token_type": "bearer"}


@router.post("/auth/wechat-login")
def wechat_login(payload: dict, request: Request, session: SessionDep):
    """Exchange a trusted WeChat openid supplied by an upstream OAuth gateway."""
    # This endpoint does not perform the OAuth code exchange itself. Until a
    # verified gateway signature is implemented, never accept client-supplied
    # OpenIDs in production (otherwise anyone could impersonate a user).
    if settings.environment in {"staging", "production"}:
        raise HTTPException(status_code=503, detail="微信 OAuth 网关尚未接入")
    openid = str(payload.get("openid", "")).strip()
    if not openid or len(openid) > 64 or not payload.get("state"):
        raise HTTPException(status_code=400, detail="微信登录参数无效")
    consumer = session.exec(select(Consumer).where(Consumer.openid == openid)).first()
    if not consumer:
        consumer = Consumer(openid=openid, nickname=payload.get("nickname"))
        session.add(consumer); session.commit(); session.refresh(consumer)
    return {"access_token": make_token(str(consumer.id), "consumer", 1440), "token_type": "bearer"}


@router.post("/scan/validate")
def scan_validate(payload: ScanValidateRequest, session: SessionDep, consumer: Consumer = Depends(current_consumer)):
    enforce_rate_limit(f"rate:h5-scan:{consumer.id}", 30, 60)
    return validate_scan(session, consumer, payload.token)


@router.post("/scan/verify-redemption-code")
def verify_scan_redemption_code(payload: RedemptionCodeVerifyRequest, session: SessionDep, consumer: Consumer = Depends(current_consumer)):
    enforce_rate_limit(f"rate:h5-redemption-code:{consumer.id}", 8, 300)
    return verify_redemption_code(session, consumer, payload.verification_ticket, payload.redemption_code)


@router.get("/activities/{activity_id}")
def activity_detail(activity_id: int, session: SessionDep, _consumer: Consumer = Depends(current_consumer), lottery_policy_id: int | None = Query(default=None)):
    cache_key = f"h5:activity:{activity_id}:detail" if not lottery_policy_id else ""
    if not lottery_policy_id and (cached := redis_client.get(cache_key)):
        return json.loads(cached)
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(status_code=404, detail="活动不存在")
    if lottery_policy_id:
        policy = session.get(LotteryPolicy, lottery_policy_id)
        if not policy or policy.activity_id != activity.id or policy.status != "active":
            raise HTTPException(status_code=404, detail="抽奖策略不存在或已失效")
        pools = session.exec(select(LotteryPolicyPrize, Prize).join(Prize, Prize.id == LotteryPolicyPrize.prize_id).where(LotteryPolicyPrize.policy_id == policy.id, Prize.status == "active", Prize.type.in_([PrizeType.CASH, PrizeType.UPGRADE])).order_by(LotteryPolicyPrize.id)).all()
    else:
        pools = session.exec(select(ActivityPrize, Prize).join(Prize, Prize.id == ActivityPrize.prize_id).where(ActivityPrize.activity_id == activity.id, Prize.status == "active", Prize.type.in_([PrizeType.CASH, PrizeType.UPGRADE])).order_by(ActivityPrize.id)).all()
    result = {
        "id": activity.id,
        "name": activity.name,
        "description": activity.description or "",
        "rules": generate_activity_rules(activity),
        "prizes": [{"id": prize.id, "name": prize.name, "type": prize.type, "cash_amount": str(prize.cash_amount), "upgrade_price": str(prize.upgrade_price)} for _pool, prize in pools],
    }
    if not lottery_policy_id:
        redis_client.setex(cache_key, 300, json.dumps(result))
    return result


@router.post("/lottery/draw")
def lottery_draw(payload: DrawRequest, request: Request, session: SessionDep, consumer: Consumer = Depends(current_consumer)):
    enforce_rate_limit(f"rate:h5-draw:{consumer.id}", 12, 60)
    client_ip = resolve_client_ip(request)
    return draw(session, consumer, payload.draw_ticket, payload.idempotency_key, client_ip=client_ip, client_location=payload.client_location)


@router.get("/lottery/records")
def records(session: SessionDep, consumer: Consumer = Depends(current_consumer), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    base = select(LotteryRecord).where(LotteryRecord.consumer_id == consumer.id)
    total = session.exec(select(LotteryRecord.id).where(LotteryRecord.consumer_id == consumer.id)).all()
    record_ids = session.exec(base.order_by(LotteryRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    if not record_ids:
        return {"items": [], "page": page, "page_size": page_size, "total": len(total), "has_next": False}
    ids = [record.id for record in record_ids]
    rows = session.exec(select(LotteryRecord, QrCode, Activity, CashPayment, UpgradeOrder).join(QrCode, QrCode.id == LotteryRecord.qrcode_id).join(Activity, Activity.id == LotteryRecord.activity_id).outerjoin(CashPayment, CashPayment.lottery_record_id == LotteryRecord.id).outerjoin(UpgradeOrder, UpgradeOrder.lottery_record_id == LotteryRecord.id).where(LotteryRecord.id.in_(ids))).all()
    by_id = {record.id: (record, code, activity, payment, upgrade) for record, code, activity, payment, upgrade in rows}
    items = []
    for record_id in ids:
        record, code, activity, payment, upgrade = by_id[record_id]
        items.append({**public_record(record, payment), "code_no": code.code_no, "activity_name": activity.name, "upgrade_redeem_code": upgrade.redeem_code if upgrade else None, "upgrade_amount": str(upgrade.amount) if upgrade else None, "upgrade_status": upgrade.status if upgrade else None, "upgrade_expires_at": upgrade.expires_at.isoformat() if upgrade else None})
    return {"items": items, "page": page, "page_size": page_size, "total": len(total), "has_next": page * page_size < len(total)}


@router.get("/wechat/js-config")
def wechat_js_config(consumer: Consumer = Depends(current_consumer), url: str = Query(min_length=1, max_length=2048)):
    enforce_rate_limit(f"rate:h5-js-config:{consumer.id}", 30, 60)
    return build_jsapi_config(url)


@router.post("/lottery/records/{record_id}/cash-claim")
def cash_claim(record_id: int, session: SessionDep, consumer: Consumer = Depends(current_consumer)):
    """H5 用户点击现金奖“领取/确认收款”时调用。

    返回 result=claim 时前端再用 js-config + requestMerchantTransfer 拉起
    微信确认收款页；其他状态（success/failed/processing/manual）原样返回，
    由前端按文案展示。
    """
    enforce_rate_limit(f"rate:h5-cash-claim:{consumer.id}", 12, 60)
    record = session.get(LotteryRecord, record_id)
    if not record or record.consumer_id != consumer.id:
        raise HTTPException(status_code=404, detail="中奖记录不存在")
    if record.prize_type != PrizeType.CASH:
        raise HTTPException(status_code=422, detail="该奖品不是现金奖，无需领取")
    payment = session.exec(
        select(CashPayment).where(CashPayment.lottery_record_id == record.id)
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="现金奖励订单不存在，请联系客服")
    try:
        return claim_cash_payment(payment.id)
    except WechatPayConfigError as exc:
        raise HTTPException(status_code=503, detail=f"自动打款尚未完成配置：{exc}") from exc
    except TransferQueryFailed as exc:
        raise HTTPException(status_code=503, detail="微信支付暂时不可用，请稍后再试") from exc


@router.get("/points")
def point_wallet(session: SessionDep, consumer: Consumer = Depends(current_consumer)):
    raise HTTPException(status_code=410, detail="MVP 不提供积分功能")
