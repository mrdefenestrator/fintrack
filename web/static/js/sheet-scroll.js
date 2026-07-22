/**
 * Sheet scroll shadows (QA item 15 / 2).
 *
 * Opt-in mechanism: any scrolling container marked with the `data-sheet-scroll`
 * attribute (the element with `overflow: auto` — e.g. `.table-scroll-container`
 * used by the accounts/budget/assets/transactions/merchants sheet pages)
 * automatically gets two shadow overlays:
 *
 *   - `.sheet-scroll-shadow--top`    shown once the sheet is scrolled down from
 *                                    the top (a subtle shadow under the sticky
 *                                    header row).
 *   - `.sheet-scroll-shadow--bottom` shown until the sheet is scrolled all the
 *                                    way to the bottom (a subtle shadow above
 *                                    the sticky total row, or the table's bottom
 *                                    edge when there is no total row).
 *
 * Crucially, the overlays must NOT live inside the scrolling element: an
 * absolutely-positioned child of an `overflow:auto` box scrolls with the
 * content, so the shadows would drift out of view (the top one never appeared;
 * the bottom one never pinned — QA item 2). Instead this script wraps each
 * container in a non-scrolling `.sheet-scroll-frame` and appends the overlays
 * to that frame, so they stay pinned against the container's viewport. The
 * frame inherits the container's flex sizing so page layout is unchanged.
 *
 * Both fade in/out via the `.is-visible` class (see base.html for the box
 * styling). To adopt this on another table, just add `data-sheet-scroll` to its
 * scrolling container — no other wiring is required.
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

    // Wrap the scrolling container in a non-scrolling positioned frame (once)
    // and return it. The shadow overlays are attached to this frame so they do
    // not scroll with the container's content.
    function ensureFrame(container) {
        var parent = container.parentElement;
        if (parent && parent.classList.contains("sheet-scroll-frame")) {
            return parent;
        }
        var frame = document.createElement("div");
        frame.className = "sheet-scroll-frame";
        container.parentNode.insertBefore(frame, container);
        frame.appendChild(container);
        return frame;
    }

    function ensureShadowEls(frame) {
        function ensure(edge) {
            var el = frame.querySelector(":scope > .sheet-scroll-shadow--" + edge);
            if (!el) {
                el = document.createElement("div");
                el.className = "sheet-scroll-shadow sheet-scroll-shadow--" + edge;
                el.setAttribute("aria-hidden", "true");
                frame.appendChild(el);
            }
            return el;
        }
        return {
            top: ensure("top"),
            bottom: ensure("bottom"),
            left: ensure("left"),
            right: ensure("right"),
        };
    }

    // The overlays read their offsets from CSS vars; set them on the frame
    // (their positioned ancestor) so they inherit down.
    function measure(frame, container) {
        var thead = container.querySelector("thead");
        var headerH = thead ? thead.offsetHeight : 0;
        if (!thead) {
            // Grouped sheet (no thead): the sticky heading + column-header rows
            // form the pinned top region (same height for every group). The
            // column header sticks just below the heading, so publish the
            // heading height too (its CSS `top`).
            var heading = container.querySelector(".holdings-group-heading");
            var header = container.querySelector(".holdings-group-header");
            var headingH = heading ? heading.offsetHeight : 0;
            headerH = headingH + (header ? header.offsetHeight : 0);
            frame.style.setProperty("--sheet-heading-h", headingH + "px");
        }
        // Sum every total row (the Holdings footer has two: Liquid + Net worth)
        // so the side shadows stop above the whole pinned footer.
        var footerH = 0;
        container.querySelectorAll("tr.total-row").forEach(function (r) {
            footerH += r.offsetHeight;
        });
        // When a sheet pins an actions column to the right edge, keep the right
        // shadow to the left of it (over the scrolling data), not under it.
        var rightW = 0;
        if (container.hasAttribute("data-sticky-actions")) {
            var actions = container.querySelector(".table-actions-cell");
            rightW = actions ? actions.offsetWidth : 0;
        }
        frame.style.setProperty("--sheet-header-h", headerH + "px");
        frame.style.setProperty("--sheet-footer-h", footerH + "px");
        frame.style.setProperty("--sheet-right-h", rightW + "px");
    }

    var ROW_H = 28; // 1.75rem data-row height

    // Inject (once) a single filler row and grow it to push the total row to the
    // sheet's bottom (QA item 17), with a grid canvas filling the gap. It sits
    // just above the total row, in the total row's OWN tbody: on the grouped
    // Holdings sheet the total lives in a trailing tbody of its own, so this
    // keeps the canvas below the last group instead of wedging it between groups
    // (and lets each group's add row flow inline right under its data). Kept in
    // place on every call.
    function ensureFiller(container) {
        var totalRow = container.querySelector("tr.total-row");
        var anchor = totalRow;
        var tbody = anchor
            ? anchor.parentElement
            : container.querySelector("tbody:last-of-type");
        if (!tbody) return null;
        var filler = container.querySelector(".sheet-grid-filler");
        if (!filler) {
            filler = document.createElement("tr");
            filler.className = "sheet-grid-filler";
            filler.setAttribute("aria-hidden", "true");
            var td = document.createElement("td");
            td.className = "sheet-grid-filler-cell";
            td.colSpan = container.querySelectorAll("thead th").length || 100;
            filler.appendChild(td);
        }
        // Keep it directly before the total row (or at the end when there is no
        // total row — then it just fills the sheet's bottom).
        if (anchor) {
            if (filler.parentElement !== tbody || filler.nextElementSibling !== anchor) {
                tbody.insertBefore(filler, anchor);
            }
        } else if (filler.parentElement !== tbody) {
            tbody.appendChild(filler);
        }
        return filler;
    }

    // Size the filler to exactly fill the leftover vertical space (only when
    // there's at least ~one empty row's worth), else hide it.
    function sizeFiller(container, filler) {
        if (!filler) return;
        // Collapse it, then measure the real content height (the table's — the
        // container's scrollHeight is clamped to clientHeight when content is
        // short, so it can't tell us the gap).
        filler.style.display = "none";
        var table = container.querySelector("table");
        var contentH = table ? table.offsetHeight : 0;
        var gap = container.clientHeight - contentH;
        if (gap >= ROW_H) {
            filler.firstChild.style.height = gap + "px";
            filler.style.display = "";
        }
    }

    function update(container) {
        var frame = ensureFrame(container);
        var els = ensureShadowEls(frame);
        measure(frame, container);
        sizeFiller(container, ensureFiller(container));

        var atTop = container.scrollTop <= TOLERANCE;
        var atBottom =
            container.scrollTop + container.clientHeight >=
            container.scrollHeight - TOLERANCE;
        // Nothing to scroll at all: never show either shadow.
        var isScrollableY =
            container.scrollHeight - container.clientHeight > TOLERANCE;

        var atLeft = container.scrollLeft <= TOLERANCE;
        var atRight =
            container.scrollLeft + container.clientWidth >=
            container.scrollWidth - TOLERANCE;
        var isScrollableX =
            container.scrollWidth - container.clientWidth > TOLERANCE;

        els.top.classList.toggle("is-visible", isScrollableY && !atTop);
        els.bottom.classList.toggle("is-visible", isScrollableY && !atBottom);
        els.left.classList.toggle("is-visible", isScrollableX && !atLeft);
        els.right.classList.toggle("is-visible", isScrollableX && !atRight);
    }

    // Persist the scroll position per page so it survives a full reload (delete
    // / add do HX-Refresh). Cell-edit swaps only replace the table's innerHTML,
    // so the container scroll is already kept there.
    function scrollKey() {
        return "sheetScroll:" + window.location.pathname;
    }
    function saveScroll(container) {
        try {
            sessionStorage.setItem(
                scrollKey(),
                JSON.stringify({ x: container.scrollLeft, y: container.scrollTop })
            );
        } catch (e) { /* ignore */ }
    }
    function restoreScroll(container) {
        try {
            var s = JSON.parse(sessionStorage.getItem(scrollKey()));
            if (s) {
                container.scrollLeft = s.x || 0;
                container.scrollTop = s.y || 0;
            }
        } catch (e) { /* ignore */ }
    }

    function init(container) {
        if (container.dataset.sheetScrollBound) {
            update(container);
            return;
        }
        container.dataset.sheetScrollBound = "true";
        var frame = ensureFrame(container);
        ensureShadowEls(frame);
        restoreScroll(container);

        container.addEventListener("scroll", function () {
            update(container);
            saveScroll(container);
        }, { passive: true });

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
