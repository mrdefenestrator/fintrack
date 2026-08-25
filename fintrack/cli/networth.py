"""Net-worth command groups: status, accounts, budget, income/expenses,
assets/debts, funding (ported from the finances argparse CLI)."""

from datetime import date

import click

from fintrack.accounts import repository as repo_accounts
from fintrack.budget import repository as repo_budget
from fintrack.core.filters import (
    apply_budget_filters,
    filter_accounts_by_type,
    filter_assets_by_kind,
)
from fintrack.core.formatting import fmt_money
from fintrack.core.loader import load_finances_from_db
from fintrack.core.tables import (
    _account_display_by_id,
    _append_table_separator_and_total,
    _build_accounts_table,
    _build_budget_table,
    _build_funding_table,
    _build_net_worth_table,
)
from fintrack.cli.helpers import (
    drop_separator_rows,
    echo_table,
    pass_cli,
    sort_items,
    sort_options,
)
from fintrack.core.types import ASSET_TYPE_VALUES
from fintrack.networth import repository as repo_assets
from fintrack.networth.calculations import (
    _ACCOUNT_TYPE_TO_CALCULATION,
    ACCOUNT_TYPES,
    BUDGET_KINDS,
    RECURRENCE_OPTIONS,
    account_funding_needed,
    liquid_minus_cc,
    net_nonliquid_total,
    projected_change_to_eom,
)
from fintrack.snapshots import repository as repo_snapshots

_ACCOUNTS_COLALIGN = ("left", "left", "left", "right", "right", "right", "right")
_BUDGET_COLALIGN = (
    "left",
    "left",
    "left",
    "right",
    "right",
    "right",
    "left",
    "left",
    "left",
)
_ASSETS_COLALIGN = (
    "left",
    "left",
    "left",
    "right",
    "right",
    "right",
    "left",
    "right",
    "right",
    "right",
    "right",
    "right",
    "right",
    "left",
)

# Asset-entry types selectable for the `asset` kind (everything but `loan`,
# which is the liability type used by debts). Drives --type choices.
_ASSET_ONLY_TYPES = [t for t in ASSET_TYPE_VALUES if t != "loan"]


def _load(cli, conn):
    snapshot_id = cli.snapshot_id(conn)
    return snapshot_id, load_finances_from_db(conn, snapshot_id)


@click.command()
@pass_cli
def status(cli):
    """Key-numbers rollup: accounts, budget to end of month, assets."""
    with cli.connect() as conn:
        _, data = _load(cli, conn)
    accounts = data.get("accounts") or []
    budget = data.get("budget") or []
    assets = data.get("assets") or []
    rates = data.get("rates")
    today = date.today()
    n2 = liquid_minus_cc(accounts)
    n3 = projected_change_to_eom(budget, today.year, today.month, today.day)
    n6 = net_nonliquid_total(assets, rates=rates)
    rows = [
        ["Accounts", fmt_money(n2)],
        ["Budget (prorated)", fmt_money(n3)],
        ["Assets", fmt_money(n6)],
    ]
    headers = ["Kind", "Amount"]
    _append_table_separator_and_total(rows, headers, ["Total", fmt_money(n2 + n3 + n6)])
    echo_table(rows, headers, ("left", "right"))


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


@click.group()
def snapshots():
    """Manage snapshots (independent households)."""


@snapshots.command("list")
@pass_cli
def snapshots_list(cli):
    with cli.connect() as conn:
        for name in repo_snapshots.list_snapshots(conn):
            click.echo(name)


@snapshots.command("add")
@click.argument("name")
@pass_cli
def snapshots_add(cli, name):
    with cli.connect() as conn:
        if repo_snapshots.get_snapshot_id(conn, name) is not None:
            raise click.ClickException(f"Snapshot already exists: {name}")
        repo_snapshots.create_snapshot(conn, name)
    click.echo(f"Created snapshot: {name}")


@snapshots.command("copy")
@click.argument("source")
@click.argument("dest")
@pass_cli
def snapshots_copy(cli, source, dest):
    with cli.connect() as conn:
        from_id = repo_snapshots.get_snapshot_id(conn, source)
        if from_id is None:
            raise click.ClickException(f"Snapshot not found: {source}")
        if repo_snapshots.get_snapshot_id(conn, dest) is not None:
            raise click.ClickException(f"Snapshot already exists: {dest}")
        repo_snapshots.copy_snapshot(conn, from_id, dest)
    click.echo(f"Copied snapshot {source} -> {dest}")


@snapshots.command("rename")
@click.argument("old_name")
@click.argument("new_name")
@pass_cli
def snapshots_rename(cli, old_name, new_name):
    with cli.connect() as conn:
        snapshot_id = repo_snapshots.get_snapshot_id(conn, old_name)
        if snapshot_id is None:
            raise click.ClickException(f"Snapshot not found: {old_name}")
        if repo_snapshots.get_snapshot_id(conn, new_name) is not None:
            raise click.ClickException(f"Snapshot already exists: {new_name}")
        repo_snapshots.rename_snapshot(conn, snapshot_id, new_name)
    click.echo(f"Renamed snapshot {old_name} -> {new_name}")


@snapshots.command("delete")
@click.argument("name")
@click.confirmation_option(
    prompt="Delete the snapshot and everything scoped to it (accounts, "
    "transactions, budget, assets)?"
)
@pass_cli
def snapshots_delete(cli, name):
    with cli.connect() as conn:
        snapshot_id = repo_snapshots.get_snapshot_id(conn, name)
        if snapshot_id is None:
            raise click.ClickException(f"Snapshot not found: {name}")
        repo_snapshots.delete_snapshot(conn, snapshot_id)
    click.echo(f"Deleted snapshot: {name}")


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------


@click.group()
def accounts():
    """Manage accounts (unified: import targets + balances/funding)."""


@accounts.command("list")
@click.option(
    "-i", "--include-type", "include_types", multiple=True, help="Only these types"
)
@click.option(
    "-x", "--exclude-type", "exclude_types", multiple=True, help="Exclude these types"
)
@sort_options
@pass_cli
def accounts_list(cli, include_types, exclude_types, sort_key, sort_dir, show_id):
    with cli.connect() as conn:
        _, data = _load(cli, conn)
    accounts_data = filter_accounts_by_type(
        data.get("accounts") or [], list(include_types), list(exclude_types)
    )
    if not accounts_data:
        return
    if sort_key:
        accounts_data = sort_items(accounts_data, sort_key, sort_dir == "desc")
    n2 = liquid_minus_cc(accounts_data)
    headers, rows = _build_accounts_table(
        accounts_data,
        n2,
        show_id=show_id,
        account_display_by_id=_account_display_by_id(accounts_data),
    )
    echo_table(rows, headers, _ACCOUNTS_COLALIGN, show_id)


def _account_field_options(f):
    for decorator in reversed(
        [
            click.option("--balance", type=float, default=None),
            click.option("--limit", "limit_", type=float, default=None),
            click.option("--available", type=float, default=None),
            click.option("--rewards_balance", type=float, default=None),
            click.option("--statement_balance", type=float, default=None),
            click.option("--statement_due_day_of_month", type=int, default=None),
            click.option("--institution", default=None),
            click.option("--asOfDate", "as_of", default=None),
            click.option("--minimum_balance", type=float, default=None),
        ]
    ):
        f = decorator(f)
    return f


def _account_updates(kwargs) -> dict:
    field_map = {
        "balance": "balance",
        "limit_": "limit",
        "available": "available",
        "rewards_balance": "rewards_balance",
        "statement_balance": "statement_balance",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "institution": "institution",
        "as_of": "asOfDate",
        "minimum_balance": "minimum_balance",
    }
    return {
        field: kwargs[arg]
        for arg, field in field_map.items()
        if kwargs.get(arg) is not None
    }


@accounts.command("add")
@click.option("--name", required=True)
@click.option("--type", "account_type", required=True, type=click.Choice(ACCOUNT_TYPES))
@_account_field_options
@pass_cli
def accounts_add(cli, name, account_type, **kwargs):
    account = {"name": name, "type": account_type}
    account.update(_account_updates(kwargs))
    if account_type == "credit_card":
        if "limit" not in account or "available" not in account:
            raise click.ClickException("credit_card requires --limit and --available")
    elif "balance" not in account:
        account["balance"] = 0
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        try:
            new_id = repo_accounts.add_account(conn, snapshot_id, account)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Added account id {new_id}: {name}")


@accounts.command("edit")
@click.argument("account_id", type=int)
@click.option("--name", default=None)
@click.option("--type", "account_type", type=click.Choice(ACCOUNT_TYPES), default=None)
@_account_field_options
@pass_cli
def accounts_edit(cli, account_id, name, account_type, **kwargs):
    updates = _account_updates(kwargs)
    if name is not None:
        updates["name"] = name
    if account_type is not None:
        updates["type"] = account_type
    if not updates:
        raise click.ClickException("specify at least one field to update")
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        try:
            repo_accounts.update_account(conn, snapshot_id, account_id, updates)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Updated account id {account_id}")


@accounts.command("delete")
@click.argument("account_id", type=int)
@click.option("--dry-run", is_flag=True)
@pass_cli
def accounts_delete(cli, account_id, dry_run):
    """Delete an account; its imports and transactions cascade with it."""
    with cli.connect() as conn:
        snapshot_id, data = _load(cli, conn)
        if dry_run:
            acc = next((a for a in data["accounts"] if a.get("id") == account_id), None)
            if not acc:
                raise click.ClickException(f"Account id {account_id} not found")
            click.echo(f"Would delete account id {account_id}: {acc.get('name', '?')}")
            return
        try:
            repo_accounts.delete_account(conn, snapshot_id, account_id)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Deleted account id {account_id}")


# ---------------------------------------------------------------------------
# budget / income / expenses
# ---------------------------------------------------------------------------


@click.command()
@click.option("--kind", "include_kinds", multiple=True, type=click.Choice(BUDGET_KINDS))
@click.option("-i", "--include-category", "include_categories", multiple=True)
@click.option("-x", "--exclude-category", "exclude_categories", multiple=True)
@click.option("--include-recurrence", "include_recurrence", multiple=True)
@click.option("--exclude-recurrence", "exclude_recurrence", multiple=True)
@sort_options
@pass_cli
def budget(
    cli,
    include_kinds,
    include_categories,
    exclude_categories,
    include_recurrence,
    exclude_recurrence,
    sort_key,
    sort_dir,
    show_id,
):
    """Income and expenses together, with subtotals for this month."""
    with cli.connect() as conn:
        _, data = _load(cli, conn)
    entries = apply_budget_filters(
        data.get("budget") or [],
        include_kinds=list(include_kinds) or None,
        include_categories=list(include_categories) or None,
        exclude_categories=list(exclude_categories) or None,
        include_recurrence=list(include_recurrence) or None,
        exclude_recurrence=list(exclude_recurrence) or None,
    )
    if not entries:
        return
    if sort_key:
        entries = sort_items(entries, sort_key, sort_dir == "desc")
    today = date.today()
    headers, rows = _build_budget_table(
        entries,
        today.year,
        today.month,
        today.day,
        account_display_by_id=_account_display_by_id(data.get("accounts") or []),
        show_index=show_id,
    )
    echo_table(rows, headers, _BUDGET_COLALIGN, show_id)


def _budget_entry_options(kind: str, adding: bool):
    def wrap(f):
        for decorator in reversed(
            [
                click.option("--description", required=adding, default=None),
                click.option("--amount", type=float, required=adding, default=None),
                click.option(
                    "--recurrence",
                    type=click.Choice(RECURRENCE_OPTIONS),
                    required=adding,
                    default=None,
                ),
                click.option("--category", default=None),
                click.option("--date", "date_", default=None),
                click.option("--dayOfMonth", "day_of_month", type=int, default=None),
                click.option("--month", type=int, default=None),
                click.option("--dayOfYear", "day_of_year", type=int, default=None),
                click.option("--continuous", is_flag=True, default=None),
                click.option(
                    "--autoAccountRef", "auto_account_ref", type=int, default=None
                ),
            ]
        ):
            f = decorator(f)
        return f

    return wrap


def _budget_fields(kwargs) -> dict:
    field_map = {
        "description": "description",
        "amount": "amount",
        "recurrence": "recurrence",
        "category": "category",
        "date_": "date",
        "day_of_month": "dayOfMonth",
        "month": "month",
        "day_of_year": "dayOfYear",
        "auto_account_ref": "autoAccountRef",
    }
    fields = {
        field: kwargs[arg]
        for arg, field in field_map.items()
        if kwargs.get(arg) is not None
    }
    if kwargs.get("continuous"):
        fields["continuous"] = True
    return fields


def _kind_entries(budget_entries: list, kind: str) -> list:
    return [e for e in budget_entries if e.get("kind") == kind]


def _global_index(budget_entries: list, kind: str, index: int, label: str) -> int:
    kind_entries = _kind_entries(budget_entries, kind)
    if index < 0 or index >= len(kind_entries):
        raise click.ClickException(f"{label} index {index} out of range")
    return budget_entries.index(kind_entries[index])


def make_budget_kind_group(kind: str, label: str) -> click.Group:
    """income and expenses share their whole command surface."""

    @click.group(
        name=kind if kind != "expense" else "expenses",
        invoke_without_command=True,
        help=f"{label.capitalize()} budget entries; bare invocation lists them.",
    )
    @sort_options
    @click.pass_context
    def group(ctx, sort_key, sort_dir, show_id):
        if ctx.invoked_subcommand is not None:
            return
        cli = ctx.obj
        with cli.connect() as conn:
            _, data = _load(cli, conn)
        entries = _kind_entries(data.get("budget") or [], kind)
        if sort_key:
            entries = sort_items(entries, sort_key, sort_dir == "desc")
        today = date.today()
        headers, rows = _build_budget_table(
            entries,
            today.year,
            today.month,
            today.day,
            account_display_by_id=_account_display_by_id(data.get("accounts") or []),
            show_index=show_id,
        )
        rows = drop_separator_rows(rows)
        if not rows:
            return
        echo_table(rows, headers, _BUDGET_COLALIGN, show_id)

    @group.command("add")
    @_budget_entry_options(kind, adding=True)
    @pass_cli
    def add(cli, **kwargs):
        entry = {"kind": kind}
        entry.update(_budget_fields(kwargs))
        with cli.connect() as conn:
            snapshot_id = cli.snapshot_id(conn)
            try:
                repo_budget.add_budget_entry(conn, snapshot_id, entry)
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"Added {label}: {entry['description']}")

    @group.command("edit")
    @click.argument("index", type=int)
    @_budget_entry_options(kind, adding=False)
    @pass_cli
    def edit(cli, index, **kwargs):
        updates = _budget_fields(kwargs)
        if not updates:
            raise click.ClickException("specify at least one field to update")
        with cli.connect() as conn:
            snapshot_id, data = _load(cli, conn)
            global_idx = _global_index(
                data.get("budget") or [], kind, index, label.capitalize()
            )
            try:
                repo_budget.update_budget_entry(conn, snapshot_id, global_idx, updates)
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"Updated {label} at index {index}")

    @group.command("delete")
    @click.argument("index", type=int)
    @click.option("--dry-run", is_flag=True)
    @pass_cli
    def delete(cli, index, dry_run):
        with cli.connect() as conn:
            snapshot_id, data = _load(cli, conn)
            budget_entries = data.get("budget") or []
            global_idx = _global_index(budget_entries, kind, index, label.capitalize())
            if dry_run:
                entry = _kind_entries(budget_entries, kind)[index]
                click.echo(
                    f"Would delete {label} at index {index}: "
                    f"{entry.get('description', '?')}"
                )
                return
            try:
                repo_budget.delete_budget_entry(conn, snapshot_id, global_idx)
            except ValueError as e:
                raise click.ClickException(str(e))
        click.echo(f"Deleted {label} at index {index}")

    return group


income = make_budget_kind_group("income", "income")
expenses = make_budget_kind_group("expense", "expense")


# ---------------------------------------------------------------------------
# assets / debts
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option(
    "--kind", "include_kinds", multiple=True, type=click.Choice(["asset", "debt"])
)
@sort_options
@click.pass_context
def assets(ctx, include_kinds, sort_key, sort_dir, show_id):
    """Assets and debts (net worth); subcommands manage assets."""
    if ctx.invoked_subcommand is not None:
        return
    cli = ctx.obj
    with cli.connect() as conn:
        _, data = _load(cli, conn)
    entries = filter_assets_by_kind(
        data.get("assets") or [], list(include_kinds) or None
    )
    if not entries:
        return
    if sort_key:
        entries = sort_items(entries, sort_key, sort_dir == "desc")
    headers, rows = _build_net_worth_table(entries, show_index=show_id)
    echo_table(rows, headers, _ASSETS_COLALIGN, show_id)


def _indexed_by_kind(all_entries: list, kind: str, index: int, label: str):
    indexed = [(gi, e) for gi, e in enumerate(all_entries) if e.get("kind") == kind]
    if index < 0 or index >= len(indexed):
        raise click.ClickException(f"{label} index {index} out of range")
    return indexed[index]


@assets.command("add")
@click.option("--name", required=True)
@click.option("--value", type=float, required=True)
@click.option(
    "--type",
    "asset_type",
    type=click.Choice(_ASSET_ONLY_TYPES),
    default=None,
    help="Holding type (e.g. brokerage, retirement, real_estate). "
    "Left unclassified if omitted — there is no catch-all type.",
)
@click.option("--quantity", type=float, default=None)
@click.option("--source", default=None)
@click.option("--institution", default=None)
@pass_cli
def assets_add(cli, name, value, asset_type, quantity, source, institution):
    asset = {"kind": "asset", "name": name, "value": value}
    if asset_type is not None:
        asset["type"] = asset_type
    if quantity is not None:
        asset["quantity"] = quantity
    if source:
        asset["source"] = source
    if institution:
        asset["institution"] = institution
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        try:
            new_id = repo_assets.add_asset_entry(conn, snapshot_id, asset)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Added asset id {new_id}: {name}")


@assets.command("edit")
@click.argument("index", type=int)
@click.option("--name", default=None)
@click.option("--value", type=float, default=None)
@click.option("--type", "type", type=click.Choice(_ASSET_ONLY_TYPES), default=None)
@click.option("--quantity", type=float, default=None)
@click.option("--source", default=None)
@click.option("--institution", default=None)
@pass_cli
def assets_edit(cli, index, **kwargs):
    updates = {k: v for k, v in kwargs.items() if v is not None}
    if not updates:
        raise click.ClickException("specify at least one field to update")
    with cli.connect() as conn:
        snapshot_id, data = _load(cli, conn)
        _, entry = _indexed_by_kind(data.get("assets") or [], "asset", index, "Asset")
        try:
            repo_assets.update_asset_entry(conn, snapshot_id, entry["id"], updates)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Updated asset at index {index}")


@assets.command("delete")
@click.argument("index", type=int)
@click.option("--dry-run", is_flag=True)
@pass_cli
def assets_delete(cli, index, dry_run):
    with cli.connect() as conn:
        snapshot_id, data = _load(cli, conn)
        _, entry = _indexed_by_kind(data.get("assets") or [], "asset", index, "Asset")
        if dry_run:
            click.echo(f"Would delete asset at index {index}: {entry.get('name', '?')}")
            return
        try:
            repo_assets.delete_asset_entry(conn, snapshot_id, entry["id"])
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Deleted asset at index {index}")


@click.group(invoke_without_command=True)
@sort_options
@click.pass_context
def debts(ctx, sort_key, sort_dir, show_id):
    """Debts (net worth); subcommands manage debts."""
    if ctx.invoked_subcommand is not None:
        return
    cli = ctx.obj
    with cli.connect() as conn:
        _, data = _load(cli, conn)
    all_entries = data.get("assets") or []
    debt_entries = [e for e in all_entries if e.get("kind") == "debt"]
    if not debt_entries:
        return
    if sort_key:
        debt_entries = sort_items(debt_entries, sort_key, sort_dir == "desc")
    asset_entries = [e for e in all_entries if e.get("kind") == "asset"]
    headers, rows = _build_net_worth_table(
        asset_entries + debt_entries, show_index=show_id
    )
    index_offset = 1 if show_id else 0
    debt_rows = [
        r for r in rows if len(r) >= (1 + index_offset) and r[index_offset] == "Debt"
    ]
    if debt_rows:
        echo_table(debt_rows, headers, _ASSETS_COLALIGN, show_id)


@debts.command("add")
@click.option("--name", required=True)
@click.option("--balance", type=float, required=True)
@click.option("--quantity", type=float, default=None)
@click.option("--assetRef", "asset_ref", type=int, default=None)
@click.option("--interestRate", "interest_rate", type=float, default=None)
@click.option("--original-principal", type=float, default=None)
@click.option("--term-months", type=click.IntRange(min=1), default=None)
@click.option(
    "--statement_due_day_of_month",
    type=click.IntRange(min=1, max=31),
    default=None,
)
@click.option("--origination-date", default=None)
@click.option("--asOfDate", "as_of", default=None)
@click.option("--institution", default=None)
@pass_cli
def debts_add(
    cli,
    name,
    balance,
    quantity,
    asset_ref,
    interest_rate,
    original_principal,
    term_months,
    statement_due_day_of_month,
    origination_date,
    as_of,
    institution,
):
    entry = {"kind": "debt", "name": name, "balance": balance, "type": "loan"}
    for field, value in (
        ("quantity", quantity),
        ("assetRef", asset_ref),
        ("interestRate", interest_rate),
        ("originalPrincipal", original_principal),
        ("termMonths", term_months),
        ("statement_due_day_of_month", statement_due_day_of_month),
        ("originationDate", origination_date),
        ("asOfDate", as_of),
        ("institution", institution),
    ):
        if value is not None:
            entry[field] = value
    with cli.connect() as conn:
        snapshot_id = cli.snapshot_id(conn)
        try:
            repo_assets.add_asset_entry(conn, snapshot_id, entry)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Added debt: {name}")


@debts.command("edit")
@click.argument("index", type=int)
@click.option("--name", default=None)
@click.option("--balance", type=float, default=None)
@click.option("--quantity", type=float, default=None)
@click.option("--assetRef", "asset_ref", type=int, default=None)
@click.option("--interestRate", "interest_rate", type=float, default=None)
@click.option("--original-principal", type=float, default=None)
@click.option("--term-months", type=click.IntRange(min=1), default=None)
@click.option(
    "--statement_due_day_of_month",
    type=click.IntRange(min=1, max=31),
    default=None,
)
@click.option("--origination-date", default=None)
@click.option("--asOfDate", "as_of", default=None)
@click.option("--institution", default=None)
@pass_cli
def debts_edit(cli, index, **kwargs):
    field_map = {
        "name": "name",
        "balance": "balance",
        "quantity": "quantity",
        "asset_ref": "assetRef",
        "interest_rate": "interestRate",
        "original_principal": "originalPrincipal",
        "term_months": "termMonths",
        "statement_due_day_of_month": "statement_due_day_of_month",
        "origination_date": "originationDate",
        "as_of": "asOfDate",
        "institution": "institution",
    }
    updates = {
        field: kwargs[arg]
        for arg, field in field_map.items()
        if kwargs.get(arg) is not None
    }
    if not updates:
        raise click.ClickException("specify at least one field to update")
    with cli.connect() as conn:
        snapshot_id, data = _load(cli, conn)
        _, entry = _indexed_by_kind(data.get("assets") or [], "debt", index, "Debt")
        try:
            repo_assets.update_asset_entry(conn, snapshot_id, entry["id"], updates)
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Updated debt at index {index}")


@debts.command("delete")
@click.argument("index", type=int)
@click.option("--dry-run", is_flag=True)
@pass_cli
def debts_delete(cli, index, dry_run):
    with cli.connect() as conn:
        snapshot_id, data = _load(cli, conn)
        _, entry = _indexed_by_kind(data.get("assets") or [], "debt", index, "Debt")
        if dry_run:
            click.echo(f"Would delete debt at index {index}: {entry.get('name', '?')}")
            return
        try:
            repo_assets.delete_asset_entry(conn, snapshot_id, entry["id"])
        except ValueError as e:
            raise click.ClickException(str(e))
    click.echo(f"Deleted debt at index {index}")


# ---------------------------------------------------------------------------
# funding
# ---------------------------------------------------------------------------


@click.command()
@click.option("--reserve", type=float, default=300.0, show_default=True)
@click.option("--account-id", type=int, default=None)
@pass_cli
def funding(cli, reserve, account_id):
    """Cash each liquid account needs for CC autopay, direct expenses, reserve."""
    with cli.connect() as conn:
        _, data = _load(cli, conn)
    accounts_data = data.get("accounts") or []
    budget_data = data.get("budget") or []
    today = date.today()
    results = [
        account_funding_needed(acc, accounts_data, budget_data, today, reserve)
        for acc in accounts_data
        if _ACCOUNT_TYPE_TO_CALCULATION.get(acc.get("type")) == "liquid"
        and (account_id is None or acc.get("id") == account_id)
    ]
    if not results:
        click.echo("No eligible liquid accounts found.")
        return
    headers, rows = _build_funding_table(results)
    echo_table(
        rows,
        headers,
        ("left", "left", "left", "right", "right", "right", "right", "right"),
    )
