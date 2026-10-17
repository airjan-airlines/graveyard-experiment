from src.config import Task
from src.kb.writer import forbidden_tokens, leak_reason


def _task(**kw):
    base = dict(id="t", site="irs.gov", category="gov", start_url="", task_type="lookup", success_criteria="",
                instruction="On IRS.gov, find the form number to request a transcript. Report the form number.")
    base.update(kw)
    return Task(**base)


def test_ground_truth_rejected():
    forb = forbidden_tokens([_task(ground_truth="4506-T")], [])
    assert leak_reason({"note": "Search for Form 4506-T directly."}, forb, [])
    assert leak_reason({"note": "Use the Forms & Instructions search box, then open the PDF page."}, forb, []) is None


def test_success_answer_numbers_and_entities_rejected():
    t = _task(instruction="Find the cheapest nonstop flight from SFO to SEA. Report the airline and price.")
    forb = forbidden_tokens([t], ["Alaska Airlines, $129 round trip"])
    assert leak_reason({"note": "Alaska Airlines is usually cheapest."}, forb, [])
    assert leak_reason({"note": "Expect prices around $129."}, forb, [])
    # inputs from the instruction are not answers
    assert leak_reason({"note": "Type SFO, then pick the airport from the dropdown."}, forb, []) is None


def test_dates_rejected():
    assert leak_reason({"note": "Pick November 6 in the calendar."}, set(), [])
    assert leak_reason({"note": "Click the field, then click the day in the calendar popup."}, set(), []) is None
