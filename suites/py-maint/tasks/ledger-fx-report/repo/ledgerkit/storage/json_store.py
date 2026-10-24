"""Saving a journal as JSON and loading it back."""

import json

from ..dates import parse_date
from ..model import Journal, Posting, Transaction
from ..money import Money

FORMAT = "ledgerkit/1"


def to_json(journal):
    """The journal as JSON text: accounts, then transactions in date order."""
    document = {
        "format": FORMAT,
        "accounts": [{"name": a.name, "currency": a.currency} for a in journal.accounts.values()],
        "transactions": [{
            "date": t.date.isoformat(),
            "payee": t.payee,
            "tags": list(t.tags),
            "postings": [{"account": p.account, "amount": str(p.amount.amount), "currency": p.amount.currency,
                          "note": p.note} for p in t.postings],
        } for t in journal.transactions],
    }
    return json.dumps(document, indent=2, sort_keys=True)


def from_json(text):
    """The Journal that to_json() wrote."""
    document = json.loads(text)
    if document.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} document")
    journal = Journal()
    for account in document["accounts"]:
        journal.add_account(account["name"], account["currency"])
    for item in document["transactions"]:
        postings = [Posting(p["account"], Money(p["amount"], p["currency"]), p.get("note", ""))
                    for p in item["postings"]]
        journal.add_transaction(Transaction(parse_date(item["date"]), item["payee"], postings,
                                            tuple(item.get("tags", ()))))
    return journal
