from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest
from sqlmodel import Session

from app.models import (
    Activity,
    ActivityPrize,
    Consumer,
    Dealer,
    LotteryPolicy,
    LotteryPolicyPrize,
    Prize,
    PrizeType,
    CodeDistribution,
)
from app.services.lottery import _pick_prize, _resolve_policy


class TestPickPrize:

    def test_pick_prize_no_candidates_returns_none_prize(self):
        session = Mock(spec=Session)
        none_prize = Mock(spec=Prize)
        none_prize.type = PrizeType.NONE
        none_prize.status = "active"
        pool_none = Mock(spec=ActivityPrize)
        pool_none.prize_id = 999
        pool_none.stat_date = "2026-09-09"
        pool_none.issued_today = 0
        pool_none.daily_limit = None

        session.exec.return_value.all.return_value = [pool_none]
        session.get.return_value = none_prize

        result = _pick_prize(session, activity_id=1, consumer_id=10, policy=None)

        assert result == (pool_none, none_prize)

    def test_pick_prize_with_single_candidate_always_wins(self):
        session = Mock(spec=Session)
        cash_prize = Mock(spec=Prize)
        cash_prize.type = PrizeType.CASH
        cash_prize.status = "active"
        cash_prize.issued_stock = 0
        cash_prize.total_stock = 100
        cash_prize.per_user_limit = 10

        pool_cash = Mock(spec=ActivityPrize)
        pool_cash.prize_id = 1
        pool_cash.probability = Decimal("1.0")
        pool_cash.stat_date = "2026-09-09"
        pool_cash.issued_today = 0
        pool_cash.daily_limit = None

        session.exec.return_value.all.return_value = [pool_cash]
        session.get.return_value = cash_prize
        session.exec.return_value.one.return_value = 0

        result = _pick_prize(session, activity_id=1, consumer_id=10, policy=None)

        assert result == (pool_cash, cash_prize)

    def test_pick_prize_probability_distribution_multiple_prizes(self):
        session = Mock(spec=Session)

        prize1 = Mock(spec=Prize)
        prize1.type = PrizeType.CASH
        prize1.status = "active"
        prize1.issued_stock = 0
        prize1.total_stock = 100
        prize1.per_user_limit = 10

        prize2 = Mock(spec=Prize)
        prize2.type = PrizeType.CASH
        prize2.status = "active"
        prize2.issued_stock = 0
        prize2.total_stock = 100
        prize2.per_user_limit = 10

        pool1 = Mock(spec=ActivityPrize)
        pool1.prize_id = 1
        pool1.probability = Decimal("0.5")
        pool1.stat_date = "2026-09-09"
        pool1.issued_today = 0
        pool1.daily_limit = None

        pool2 = Mock(spec=ActivityPrize)
        pool2.prize_id = 2
        pool2.probability = Decimal("0.3")
        pool2.stat_date = "2026-09-09"
        pool2.issued_today = 0
        pool2.daily_limit = None

        session.exec.return_value.all.return_value = [pool1, pool2]
        
        def get_prize(model_class, prize_id, **kwargs):
            return prize1 if prize_id == 1 else prize2

        session.get.side_effect = get_prize
        session.exec.return_value.one.return_value = 0

        wins = {1: 0, 2: 0}
        iterations = 1000

        with patch("secrets.SystemRandom") as mock_random_class:
            for i in range(iterations):
                mock_random_instance = Mock()
                mock_random_instance.random.return_value = i / iterations
                mock_random_class.return_value = mock_random_instance

                pool, prize = _pick_prize(session, activity_id=1, consumer_id=10, policy=None)
                if prize == prize1:
                    wins[1] += 1
                elif prize == prize2:
                    wins[2] += 1

        assert wins[1] > 400
        assert wins[2] > 200
        assert wins[1] > wins[2]

    def test_pick_prize_excludes_exhausted_stock(self):
        session = Mock(spec=Session)

        exhausted_prize = Mock(spec=Prize)
        exhausted_prize.type = PrizeType.CASH
        exhausted_prize.status = "active"
        exhausted_prize.issued_stock = 100
        exhausted_prize.total_stock = 100
        exhausted_prize.per_user_limit = 10

        pool_exhausted = Mock(spec=ActivityPrize)
        pool_exhausted.prize_id = 1
        pool_exhausted.probability = Decimal("1.0")
        pool_exhausted.stat_date = "2026-09-09"
        pool_exhausted.issued_today = 0
        pool_exhausted.daily_limit = None

        session.exec.return_value.all.return_value = [pool_exhausted]
        session.get.return_value = exhausted_prize

        result = _pick_prize(session, activity_id=1, consumer_id=10, policy=None)

        assert result == (None, None)

    def test_pick_prize_excludes_daily_limit_reached(self):
        session = Mock(spec=Session)

        prize = Mock(spec=Prize)
        prize.type = PrizeType.CASH
        prize.status = "active"
        prize.issued_stock = 10
        prize.total_stock = 100
        prize.per_user_limit = 10

        pool = Mock(spec=ActivityPrize)
        pool.prize_id = 1
        pool.probability = Decimal("1.0")
        pool.stat_date = "2026-09-09"
        pool.issued_today = 50
        pool.daily_limit = 50

        session.exec.return_value.all.return_value = [pool]
        session.get.return_value = prize

        result = _pick_prize(session, activity_id=1, consumer_id=10, policy=None)

        assert result == (None, None)

    def test_pick_prize_excludes_per_user_limit_reached(self):
        session = Mock(spec=Session)

        prize = Mock(spec=Prize)
        prize.type = PrizeType.CASH
        prize.status = "active"
        prize.issued_stock = 10
        prize.total_stock = 100
        prize.per_user_limit = 5

        pool = Mock(spec=ActivityPrize)
        pool.prize_id = 1
        pool.probability = Decimal("1.0")
        pool.stat_date = "2026-09-09"
        pool.issued_today = 0
        pool.daily_limit = None

        session.exec.return_value.all.return_value = [pool]
        session.get.return_value = prize
        session.exec.return_value.one.return_value = 5

        result = _pick_prize(session, activity_id=1, consumer_id=10, policy=None)

        assert result == (None, None)

    def test_pick_prize_applies_win_probability_multiplier(self):
        session = Mock(spec=Session)

        prize = Mock(spec=Prize)
        prize.type = PrizeType.CASH
        prize.status = "active"
        prize.issued_stock = 0
        prize.total_stock = 100
        prize.per_user_limit = 10

        pool = Mock(spec=ActivityPrize)
        pool.prize_id = 1
        pool.probability = Decimal("0.5")
        pool.stat_date = "2026-09-09"
        pool.issued_today = 0
        pool.daily_limit = None

        session.exec.return_value.all.return_value = [pool]
        session.get.return_value = prize
        session.exec.return_value.one.return_value = 0

        with patch("secrets.SystemRandom") as mock_random_class:
            mock_random_instance = Mock()
            mock_random_instance.random.return_value = 0.9
            mock_random_class.return_value = mock_random_instance

            result = _pick_prize(
                session, 
                activity_id=1, 
                consumer_id=10, 
                policy=None,
                win_probability_multiplier=Decimal("2.0")
            )

            assert result == (pool, prize)


class TestResolvePolicy:

    def test_resolve_policy_no_distribution_returns_none(self):
        session = Mock(spec=Session)
        
        result = _resolve_policy(session, activity_id=1, distribution=None)
        
        assert result is None

    def test_resolve_policy_no_dealer_returns_none(self):
        session = Mock(spec=Session)
        distribution = Mock(spec=CodeDistribution)
        distribution.dealer_id = 999
        
        session.get.return_value = None
        
        result = _resolve_policy(session, activity_id=1, distribution=distribution)
        
        assert result is None

    def test_resolve_policy_dealer_scope_highest_priority(self):
        session = Mock(spec=Session)
        distribution = Mock(spec=CodeDistribution)
        distribution.dealer_id = 10
        
        dealer = Mock(spec=Dealer)
        dealer.id = 10
        dealer.province = "江苏省"
        dealer.city = "南京市"
        dealer.district = "玄武区"
        
        dealer_policy = Mock(spec=LotteryPolicy)
        dealer_policy.scope = "dealer"
        dealer_policy.version = 1
        
        session.get.return_value = dealer
        session.exec.return_value.first.return_value = dealer_policy
        
        result = _resolve_policy(session, activity_id=1, distribution=distribution)
        
        assert result == dealer_policy

    def test_resolve_policy_district_scope_fallback(self):
        session = Mock(spec=Session)
        distribution = Mock(spec=CodeDistribution)
        distribution.dealer_id = 10
        
        dealer = Mock(spec=Dealer)
        dealer.id = 10
        dealer.province = "江苏省"
        dealer.city = "南京市"
        dealer.district = "玄武区"
        
        district_policy = Mock(spec=LotteryPolicy)
        district_policy.scope = "district"
        district_policy.version = 1
        
        session.get.return_value = dealer
        
        call_count = [0]
        def first_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return district_policy
        
        session.exec.return_value.first.side_effect = first_side_effect
        
        result = _resolve_policy(session, activity_id=1, distribution=distribution)
        
        assert result == district_policy

    def test_resolve_policy_city_scope_fallback(self):
        session = Mock(spec=Session)
        distribution = Mock(spec=CodeDistribution)
        distribution.dealer_id = 10
        
        dealer = Mock(spec=Dealer)
        dealer.id = 10
        dealer.province = "江苏省"
        dealer.city = "南京市"
        dealer.district = "玄武区"
        
        city_policy = Mock(spec=LotteryPolicy)
        city_policy.scope = "city"
        city_policy.version = 1
        
        session.get.return_value = dealer
        
        call_count = [0]
        def first_side_effect():
            call_count[0] += 1
            if call_count[0] <= 2:
                return None
            return city_policy
        
        session.exec.return_value.first.side_effect = first_side_effect
        
        result = _resolve_policy(session, activity_id=1, distribution=distribution)
        
        assert result == city_policy

    def test_resolve_policy_province_scope_fallback(self):
        session = Mock(spec=Session)
        distribution = Mock(spec=CodeDistribution)
        distribution.dealer_id = 10
        
        dealer = Mock(spec=Dealer)
        dealer.id = 10
        dealer.province = "江苏省"
        dealer.city = "南京市"
        dealer.district = "玄武区"
        
        province_policy = Mock(spec=LotteryPolicy)
        province_policy.scope = "province"
        province_policy.version = 1
        
        session.get.return_value = dealer
        
        call_count = [0]
        def first_side_effect():
            call_count[0] += 1
            if call_count[0] <= 3:
                return None
            return province_policy
        
        session.exec.return_value.first.side_effect = first_side_effect
        
        result = _resolve_policy(session, activity_id=1, distribution=distribution)
        
        assert result == province_policy

    def test_resolve_policy_no_matching_policy_returns_none(self):
        session = Mock(spec=Session)
        distribution = Mock(spec=CodeDistribution)
        distribution.dealer_id = 10
        
        dealer = Mock(spec=Dealer)
        dealer.id = 10
        dealer.province = "江苏省"
        dealer.city = "南京市"
        dealer.district = "玄武区"
        
        session.get.return_value = dealer
        session.exec.return_value.first.return_value = None
        
        result = _resolve_policy(session, activity_id=1, distribution=distribution)
        
        assert result is None
