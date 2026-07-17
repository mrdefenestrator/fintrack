/**
 * Sheet scroll shadows (QA item 15).
 *
 * Opt-in mechanism: any scrolling container marked with the `data-sheet-scroll`
 * attribute (the element with `overflow: auto` — e.g. `.table-scroll-container`
 * used by the accounts/budget/assets spreadsheet pages) automatically gets two
 * shadow overlays inserted as direct children:
 *
 *   - `.sheet-scroll-shadow--top`    shown once the sheet is scrolled down from
 *                                    the top (a subtle shadow under the sticky
 *                                    header row).
 *   - `.sheet-scroll-shadow--bottom` shown until the sheet is scrolled all the
 *                                    way to the bottom (a subtle shadow above
 *                                    the sticky total row). Skipped if the
 *                                    container has no `tr.total-row`.
 *
 * Both fade out at their respective scroll extremes via the `.is-visible`
 * class (see base.html for the actual box styling). To adopt this on another
 * table, just add `data-sheet-scroll` to its scrolling container — no other
 * wiring is required.
 *
 * Re-scans on DOMContentLoaded, htmx:afterSwap/afterSettle (tbody content is
 * frequently swapped in place by htmx) and window resize, and uses a
 * ResizeObserver per container so row additions/removals that change the
 * scrollable height (without a scroll or htmx event) still update the
 * shadows correctly.
 */
(function () {
    "use strict";

    var TOLERANCE = 1; // px

    function ensureShadowEls(container) {
        var top = container.querySelector(":scope > .sheet-scroll-shadow--top");
        var bottom = container.querySelector(":scope > .sheet-scroll-shadow--bottom");
        if (!top) {
            top = document.createElement("div");
            top.className = "sheet-scroll-shadow sheet-scroll-shadow--top";
            top.setAttribute("aria-hidden", "true");
            container.appendChild(top);
        }
        if (!bottom) {
            bottom = document.createElement("div");
            bottom.className = "sheet-scroll-shadow sheet-scroll-shadow--bottom";
            bottom.setAttribute("aria-hidden", "true");
            container.appendChild(bottom);
        }
        return { top: top, bottom: bottom };
    }

    function measure(container) {
        var thead = container.querySelector("thead");
        var totalRow = container.querySelector("tr.total-row");
        container.style.setProperty(
            "--sheet-header-h",
            (thead ? thead.offsetHeight : 0) + "px"
        );
        container.style.setProperty(
            "--sheet-footer-h",
            (totalRow ? totalRow.offsetHeight : 0) + "px"
        );
        return totalRow;
    }

    function update(container) {
        var els = ensureShadowEls(container);
        var totalRow = measure(container);

        var atTop = container.scrollTop <= TOLERANCE;
        var atBottom =
            container.scrollTop + container.clientHeight >=
            container.scrollHeight - TOLERANCE;
        // Nothing to scroll at all: never show either shadow.
        var isScrollable = container.scrollHeight - container.clientHeight > TOLERANCE;

        els.top.classList.toggle("is-visible", isScrollable && !atTop);
        els.bottom.classList.toggle(
            "is-visible",
            isScrollable && !atBottom && totalRow != null
        );
    }

    function init(container) {
        if (container.dataset.sheetScrollBound) {
            update(container);
            return;
        }
        container.dataset.sheetScrollBound = "true";
        ensureShadowEls(container);

        container.addEventListener("scroll", function () { update(container); }, {
            passive: true,
        });

        if (typeof ResizeObserver !== "undefined") {
            var ro = new ResizeObserver(function () { update(container); });
            ro.observe(container);
            var table = container.querySelector("table");
            if (table) ro.observe(table);
        }

        update(container);
    }

    function scan() {
        document.querySelectorAll("[data-sheet-scroll]").forEach(init);
    }

    document.addEventListener("DOMContentLoaded", scan);
    document.addEventListener("htmx:afterSwap", scan);
    document.addEventListener("htmx:afterSettle", scan);
    window.addEventListener("resize", scan);

    // In case this script runs after DOMContentLoaded already fired (e.g.
    // loaded async/late).
    if (document.readyState !== "loading") {
        scan();
    }
})();
