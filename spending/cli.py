from datetime import date as date_type
from pathlib import Path

import click

from spending.classifier import classify_and_cache
from spending.db import get_engine, init_db
from spending.importer import run_import
from spending.repository.accounts import (
    add_account,
    delete_account,
    edit_account,
    get_account_by_id,
    get_account_by_name,
    list_accounts,
)
from spending.repository.aggregations import get_monthly_category_totals
from spending.repository.categories import (
    add_category,
    delete_category,
    edit_category,
    list_categories,
    seed_categories,
)
from spending.repository.imports import get_staging_imports


@click.group()
@click.option("--db", default="spending.db", help="Path to SQLite database")
@click.pass_context
def cli(ctx, db):
    """Spending tracker CLI."""
    ctx.ensure_object(dict)
    engine = get_engine(db)
    init_db(engine)
    ctx.obj["engine"] = engine
    ctx.call_on_close(engine.dispose)
    with engine.connect() as conn:
        seed_categories(conn, "configs/categories.yaml")


@cli.group()
def accounts():
    """Manage accounts."""


@accounts.command("list")
@click.pass_context
def accounts_list(ctx):
    """List all accounts."""
    with ctx.obj["engine"].connect() as conn:
        accts = list_accounts(conn)
    if not accts:
        click.echo("No accounts found.")
        return
    for a in accts:
        click.echo(
            f"  [{a['id']}] {a['name']} ({a['institution']}, {a['account_type']})"
        )


@accounts.command("add")
@click.option("--name", required=True)
@click.option("--institution", required=True)
@click.option(
    "--type",
    "account_type",
    required=True,
    type=click.Choice(["checking", "savings", "credit_card"]),
)
@click.pass_context
def accounts_add(ctx, name, institution, account_type):
    """Add a new account."""
    with ctx.obj["engine"].connect() as conn:
        add_account(conn, name=name, institution=institution, account_type=account_type)
    click.echo(f"Added account: {name}")


@accounts.command("edit")
@click.argument("account_id", type=int)
@click.option("--name")
@click.option("--institution")
@click.option("--type", "account_type")
@click.pass_context
def accounts_edit(ctx, account_id, name, institution, account_type):
    """Edit an account."""
    with ctx.obj["engine"].connect() as conn:
        edit_account(
            conn,
            account_id,
            name=name,
            institution=institution,
            account_type=account_type,
        )
    click.echo(f"Updated account {account_id}")


@accounts.command("delete")
@click.argument("account_id", type=int)
@click.pass_context
def accounts_delete(ctx, account_id):
    """Delete an account."""
    with ctx.obj["engine"].connect() as conn:
        acct = get_account_by_id(conn, account_id)
        if not acct:
            click.echo(f"Account {account_id} not found.")
            return
        delete_account(conn, account_id)
    click.echo(f"Deleted account {account_id}")


@cli.group()
def categories():
    """Manage categories."""


@categories.command("list")
@click.pass_context
def categories_list(ctx):
    """List all categories."""
    with ctx.obj["engine"].connect() as conn:
        cats = list_categories(conn)
    for c in cats:
        click.echo(f"  [{c['id']}] {c['name']} (order: {c['sort_order']})")


@categories.command("add")
@click.option("--name", required=True)
@click.option("--sort-order", required=True, type=int)
@click.pass_context
def categories_add(ctx, name, sort_order):
    """Add a new category."""
    with ctx.obj["engine"].connect() as conn:
        add_category(conn, name=name, sort_order=sort_order)
    click.echo(f"Added category: {name}")


@categories.command("edit")
@click.argument("category_id", type=int)
@click.option("--name")
@click.option("--sort-order", type=int)
@click.pass_context
def categories_edit(ctx, category_id, name, sort_order):
    """Edit a category."""
    with ctx.obj["engine"].connect() as conn:
        edit_category(conn, category_id, name=name, sort_order=sort_order)
    click.echo(f"Updated category {category_id}")


@categories.command("delete")
@click.argument("name")
@click.pass_context
def categories_delete(ctx, name):
    """Delete a category."""
    with ctx.obj["engine"].connect() as conn:
        delete_category(conn, name=name)
    click.echo(f"Deleted category: {name}")


@cli.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--account", help="Account name to import into")
@click.pass_context
def import_cmd(ctx, files, account):
    """Import statement files."""
    engine = ctx.obj["engine"]

    # Expand directories
    file_paths = []
    for f in files:
        p = Path(f)
        if p.is_dir():
            file_paths.extend(p.glob("*.ofx"))
            file_paths.extend(p.glob("*.qfx"))
            file_paths.extend(p.glob("*.csv"))
        else:
            file_paths.append(p)

    if not file_paths:
        click.echo("No supported files found.")
        return

    with engine.connect() as conn:
        if account:
            acct = get_account_by_name(conn, account)
            if not acct:
                click.echo(f"Account not found: {account}")
                return
            account_id = acct["id"]
        else:
            click.echo("--account is required (auto-detection not yet implemented)")
            return

        all_new_merchants = set()
        for fp in file_paths:
            click.echo(f"Importing {fp.name}...")
            result = run_import(conn, fp, account_id)

            if result.get("error"):
                click.echo(f"  Error: {result['error']}")
                continue

            click.echo(
                f"  {result['new_count']} new, "
                f"{result['skipped_count']} skipped, "
                f"{result['flagged_count']} flagged"
            )
            all_new_merchants.update(result["new_merchants"])

        # Classify new merchants
        if all_new_merchants:
            classified, warn = classify_and_cache(conn, list(all_new_merchants))
            if warn:
                click.echo(f"Warning: {warn}", err=True)
            elif classified:
                click.echo(f"Classified {classified} new merchants.")

        click.echo("Done. Review staged imports in the web UI.")


@cli.command()
@click.pass_context
def status(ctx):
    """Show current month spending summary."""
    engine = ctx.obj["engine"]
    today = date_type.today()

    with engine.connect() as conn:
        totals = get_monthly_category_totals(conn, year=today.year, month=today.month)
        staging = get_staging_imports(conn)

    if not totals:
        click.echo(f"No spending data for {today.strftime('%B %Y')}.")
    else:
        grand_total = sum(row["total"] for row in totals)
        click.echo(f"\n{today.strftime('%B %Y')} Spending: ${abs(grand_total):,.2f}")
        click.echo("-" * 40)
        for row in totals[:5]:
            click.echo(
                f"  {row['category']:20s} ${abs(row['total']):>10,.2f}  ({row['count']} txns)"
            )
        if len(totals) > 5:
            click.echo(f"  ... and {len(totals) - 5} more categories")

    if staging:
        click.echo(f"\n{len(staging)} pending import(s) awaiting review.")


@cli.command()
@click.option("--port", default=5002, type=int)
def serve(port):
    """Start the web server."""
    from web.app import create_app

    app = create_app()
    app.run(debug=True, port=port)
