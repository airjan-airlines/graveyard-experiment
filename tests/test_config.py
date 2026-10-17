import datetime as dt

from src.config import DATE_RESOLVERS, load_tasks


def test_date_resolvers():
    today = dt.date(2026, 10, 17)  # a Saturday
    assert DATE_RESOLVERS["first_friday_next_month"](today) == dt.date(2026, 11, 6)
    assert DATE_RESOLVERS["next_saturday"](today) == dt.date(2026, 10, 24)
    assert DATE_RESOLVERS["first_friday_next_month"](dt.date(2026, 12, 3)) == dt.date(2027, 1, 1)


def test_tasks_load_and_resolve(tmp_config):
    tasks = load_tasks()
    assert len(tasks) == 24
    assert all("{date:" not in t.instruction for t in tasks)
    assert all(t.task_type in ("lookup", "reach_state") for t in tasks)
