from types import SimpleNamespace

from src.runner.guards import IrreversibleActionGuard


def fake_el(text="", **attrs):
    return SimpleNamespace(attributes=attrs, ax_node=None, get_all_children_text=lambda: text)


def test_blocks_global_stop_words():
    g = IrreversibleActionGuard()
    for label in ["Place order", "PLACE ORDER", "Pay $59.95", "Confirm reservation", "Reserve now", "Submit payment"]:
        d = g.check_element(fake_el(label))
        assert d.blocked, label


def test_blocks_task_stop_words_and_aria_label():
    g = IrreversibleActionGuard(["Book", "Checkout"])
    assert g.check_element(fake_el("Book with Alaska")).blocked
    assert g.check_element(fake_el("", **{"aria-label": "Checkout"})).blocked
    ax = SimpleNamespace(attributes={}, ax_node=SimpleNamespace(name="Book"), get_all_children_text=lambda: "")
    assert g.check_element(ax).blocked


def test_allows_benign_controls():
    g = IrreversibleActionGuard(["Book", "Checkout"])
    for label in ["Booking options", "PayPal", "Payment methods", "Search", "Done", "Reservations", "Add filter",
                  "See item details", "Facebook"]:
        assert not g.check_element(fake_el(label)).blocked, label


def test_ignores_long_container_text():
    g = IrreversibleActionGuard(["Book"])
    card = "Alaska Airlines 7:00 AM - 9:05 AM nonstop $129 round trip Select flight to view Book options and fare details"
    assert not g.check_element(fake_el(card)).blocked


def test_element_without_labels():
    g = IrreversibleActionGuard()
    assert not g.check_element(SimpleNamespace(attributes=None, ax_node=None, get_all_children_text=lambda: None)).blocked
