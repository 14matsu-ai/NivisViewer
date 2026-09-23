import pytest
from app.browser_download_policy import RefreshBurst, DownloadRetryBudget


def test_burst_has_trailing_edge_and_hard_deadline():
    burst=RefreshBurst()
    assert burst.delay_ms(0)==350
    assert burst.delay_ms(0.2)==350
    assert burst.delay_ms(1.9) in (100,101)
    assert burst.delay_ms(2.0)==0
    assert burst.delay_ms(20.0)==0
    burst.reset()
    assert burst.delay_ms(20.0)==350


def test_no_idle_retry_timer():
    budget=DownloadRetryBudget()
    assert budget.next_delay_ms(0) is None
    assert budget.take_due(500)==()


def test_duplicate_failures_do_not_extend_delay():
    budget=DownloadRetryBudget()
    assert budget.failed('x',1,0)
    assert budget.failed('x',1,0.49)
    assert budget.take_due(0.5)==(('x',1),)


def test_exactly_five_opportunities_then_quiet():
    budget=DownloadRetryBudget()
    time=0.0
    for i,delay in enumerate(budget.delays):
        assert budget.failed('x',4,time)
        assert budget.take_due(time)==()
        time+=delay
        assert budget.take_due(time)==(('x',4),)
    assert not budget.failed('x',4,time)
    assert not budget.failed('x',4,time+0.1)
    assert budget.next_delay_ms(time) is None


def test_expiry_survives_new_generation():
    budget=DownloadRetryBudget(lifetime=2)
    budget.failed('x',1,0)
    assert not budget.failed('x',2,2)
    assert budget.next_delay_ms(2) is None


def test_changed_fingerprint_gets_own_attempts():
    budget=DownloadRetryBudget(delays=(1,),lifetime=30)
    budget.failed(('x','old'),1,0)
    budget.take_due(1)
    assert not budget.failed(('x','old'),1,1)
    assert budget.failed(('x','new'),2,1)
    assert budget.take_due(2)==((('x','new'),2),)


def test_capacity_does_not_evict_exhausted_records_and_restart_them():
    budget=DownloadRetryBudget(capacity=1,delays=(1,))
    budget.failed('x',1,0); budget.take_due(1)
    assert not budget.failed('y',1,1)
    assert not budget.failed('x',1,1)
    budget.clear()
    assert budget.failed('y',1,2)


def test_success_cancels_pending():
    budget=DownloadRetryBudget(); budget.failed('x',1,0); budget.resolved('x')
    assert budget.take_due(1)==()
    assert budget.next_delay_ms(1) is None


def test_batch_is_bounded_and_remainder_available():
    budget=DownloadRetryBudget()
    for i in range(20): budget.failed(i,3,0)
    assert len(budget.take_due(1))==8
    assert budget.next_delay_ms(1)==0
    assert len(budget.take_due(1))==8
    assert len(budget.take_due(1))==4
    assert budget.next_delay_ms(1) is None


def test_new_generation_updates_ticket_but_not_delay():
    budget=DownloadRetryBudget(); budget.failed('x',1,0); budget.failed('x',2,0.4)
    assert budget.take_due(.5)==(('x',2),)


@pytest.mark.parametrize('kwargs',[{'delays':()},{'delays':(0,)},{'lifetime':0},{'capacity':0}])
def test_invalid_policy_rejected(kwargs):
    with pytest.raises(ValueError): DownloadRetryBudget(**kwargs)
