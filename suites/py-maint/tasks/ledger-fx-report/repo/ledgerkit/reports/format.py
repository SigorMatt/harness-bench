"""Reports as plain-text tables."""

from ..util.text import fit


def format_table(headers, rows, numeric=()):
    """A table with a header, a rule and the rows; the columns whose index is in numeric are right-aligned."""
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
    widths = [min(width, 40) for width in widths]

    def line(cells):
        out = []
        for index, (cell, width) in enumerate(zip(cells, widths)):
            cell = fit(cell, width)
            out.append(cell.rjust(width) if index in numeric else cell.ljust(width))
        return "  ".join(out).rstrip()

    return "\n".join([line(headers), "  ".join("-" * width for width in widths)] + [line(row) for row in rows]) + "\n"


def format_balance(report):
    """A balance report as a table: account, balance in its currency, balance in the report's currency."""
    rows = [(row.account, str(row.native), str(row.converted)) for row in report.rows]
    rows.append(("Total", "", str(report.total)))
    return format_table(("Account", "Balance", f"In {report.currency}"), rows, numeric=(1, 2))


def format_income(statement):
    rows = [("Income", str(statement.income)), ("Expenses", str(statement.expenses)), ("Net", str(statement.net))]
    return format_table(("", statement.currency), rows, numeric=(1,))


def format_register(lines):
    rows = [(str(line.date), line.payee, str(line.amount), str(line.balance)) for line in lines]
    return format_table(("Date", "Payee", "Amount", "Balance"), rows, numeric=(2, 3))
