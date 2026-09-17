# 单元测试

本目录包含 lottery.py 和 wechat_pay/payout.py 关键函数的单元测试。

## 测试文件

### test_lottery_unit.py
测试 `app/services/lottery.py` 中的关键函数：

#### TestPickPrize - _pick_prize 概率测试
- `test_pick_prize_no_candidates_returns_none_prize`: 测试无候选奖品时返回"谢谢参与"
- `test_pick_prize_with_single_candidate_always_wins`: 测试单一候选奖品必中
- `test_pick_prize_probability_distribution_multiple_prizes`: 测试多奖品概率分布
- `test_pick_prize_excludes_exhausted_stock`: 测试库存耗尽的奖品被排除
- `test_pick_prize_excludes_daily_limit_reached`: 测试达到每日限额的奖品被排除
- `test_pick_prize_excludes_per_user_limit_reached`: 测试达到用户限额的奖品被排除
- `test_pick_prize_applies_win_probability_multiplier`: 测试中奖概率倍数生效

#### TestResolvePolicy - _resolve_policy 策略选择测试
- `test_resolve_policy_no_distribution_returns_none`: 测试无分发记录返回 None
- `test_resolve_policy_no_dealer_returns_none`: 测试经销商不存在返回 None
- `test_resolve_policy_dealer_scope_highest_priority`: 测试经销商级策略优先级最高
- `test_resolve_policy_district_scope_fallback`: 测试区县级策略回退
- `test_resolve_policy_city_scope_fallback`: 测试城市级策略回退
- `test_resolve_policy_province_scope_fallback`: 测试省级策略回退
- `test_resolve_policy_no_matching_policy_returns_none`: 测试无匹配策略返回 None

### test_wechat_pay_unit.py
测试 `app/services/wechat_pay/payout.py` 中的支付状态转换函数：

#### TestClearProviderBillSlots
- `test_clear_provider_bill_slots_clears_all_fields`: 测试清空商户单据字段

#### TestResetForReclaim
- `test_reset_for_reclaim_resets_payment_to_pending`: 测试重新领取时重置为 PENDING

#### TestApplyUpstreamResult - 状态转换测试
- `test_apply_upstream_result_success_state`: 测试转账成功状态转换
- `test_apply_upstream_result_fail_state`: 测试转账失败状态转换
- `test_apply_upstream_result_cancelled_state`: 测试转账撤销状态转换
- `test_apply_upstream_result_processing_state`: 测试处理中状态转换
- `test_apply_upstream_result_wait_user_confirm_state`: 测试等待用户确认状态
- `test_apply_upstream_result_no_record_still_updates_payment`: 测试无中奖记录时仍更新支付单

#### TestMarkFailed
- `test_mark_failed_sets_failed_status`: 测试标记失败状态
- `test_mark_failed_truncates_long_reason`: 测试失败原因长度截断
- `test_mark_failed_handles_missing_record`: 测试处理中奖记录缺失

#### TestPaymentStateTransitions - 状态机测试
- `test_state_transition_pending_to_processing`: PENDING → PROCESSING
- `test_state_transition_processing_to_success`: PROCESSING → SUCCESS
- `test_state_transition_processing_to_failed`: PROCESSING → FAILED
- `test_state_transition_failed_to_pending_on_reclaim`: FAILED → PENDING (重新领取)

## 运行测试

```bash
# 运行所有单元测试
pytest tests/unit/ -v

# 运行特定测试文件
pytest tests/unit/test_lottery_unit.py -v
pytest tests/unit/test_wechat_pay_unit.py -v

# 查看测试覆盖率（需要安装 pytest-cov）
pytest tests/unit/ --cov=app/services --cov-report=html
```

## 测试特点

- 使用 pytest 框架
- 使用 unittest.mock 进行隔离测试
- 不依赖数据库或外部服务
- 测试覆盖边界条件和异常场景
- 未修改业务代码，仅测试现有逻辑
