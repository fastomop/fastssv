// Tiny overlay-highlighter + editor-UX helpers for the SQL form.
//
// * Keeps a transparent <textarea> stacked over a <pre><code> mirror that
//   Prism re-highlights on every input. No framework, no build step.
// * Ctrl/Cmd + Enter submits the form from inside the textarea.
// * Delegated [data-action] handlers for Copy / Clear buttons on the editor
//   and on each suggested_fix inside the (HTMX-swapped) results panel.
(function () {
  "use strict";

  // ---- Highlight overlay -------------------------------------------------

  var ta = document.getElementById("sql");
  if (!ta) return;
  var hl = document.getElementById("sql-hl");
  var pre = hl ? hl.parentElement : null;
  var gutter = document.getElementById("sql-gutter");

  function renderGutter() {
    if (!gutter) return;
    var text = ta.value || "";
    var newlines = 0;
    for (var i = 0; i < text.length; i++) {
      if (text.charCodeAt(i) === 10) newlines++;
    }
    var count = newlines + 1;
    // One <div> per line — each is block-level so it naturally stacks
    // at line-height intervals, matching the textarea row-for-row.
    var buf = [];
    for (var n = 1; n <= count; n++) {
      buf.push("<div>" + n + "</div>");
    }
    gutter.innerHTML = buf.join("");
  }

  function update() {
    if (hl) {
      var v = ta.value;
      // Trailing-newline guard: without a trailing space the highlight layer
      // collapses the final empty line and the caret sits a row below the text.
      if (v.length === 0 || v.charAt(v.length - 1) === "\n") {
        v += " ";
      }
      hl.textContent = v;
      if (window.Prism && Prism.highlightElement) {
        Prism.highlightElement(hl);
      }
    }
    renderGutter();
  }

  // Paste doesn't always fire `input` synchronously on older browsers, and
  // programmatic .value assignments never do — dispatch an `input` after the
  // paste to make sure the gutter updates no matter how the text arrived.
  ta.addEventListener("paste", function () {
    setTimeout(update, 0);
  });
  ta.addEventListener("cut", function () {
    setTimeout(update, 0);
  });
  function sync() {
    if (pre) {
      pre.scrollTop = ta.scrollTop;
      pre.scrollLeft = ta.scrollLeft;
    }
    if (gutter) {
      gutter.scrollTop = ta.scrollTop;
    }
  }

  ta.addEventListener("input", update);
  ta.addEventListener("scroll", sync);
  update();
  sync();

  // ---- Ctrl/Cmd + Enter to submit ---------------------------------------

  ta.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      var form = ta.closest("form");
      if (form && typeof form.requestSubmit === "function") {
        form.requestSubmit();
      }
    }
  });

  // ---- Copy / Clear (delegated; works after HTMX swaps too) -------------

  function flash(btn, label) {
    // Re-entrancy guard: ignore clicks during an active flash so we don't
    // clobber the stashed innerHTML with the "Copied" text.
    if (btn.getAttribute("data-flashed") === "1") return;
    var original = btn.innerHTML;
    btn.innerHTML = label;
    btn.setAttribute("data-flashed", "1");
    setTimeout(function () {
      btn.innerHTML = original;
      btn.removeAttribute("data-flashed");
    }, 1200);
  }

  function copyText(text, btn) {
    if (!text) return;
    var done = function () { flash(btn, "Copied"); };
    var fail = function () { flash(btn, "Copy failed"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fail);
    } else {
      // Fallback for older browsers / non-secure contexts.
      try {
        var hidden = document.createElement("textarea");
        hidden.value = text;
        hidden.style.position = "fixed";
        hidden.style.opacity = "0";
        document.body.appendChild(hidden);
        hidden.select();
        document.execCommand("copy");
        document.body.removeChild(hidden);
        done();
      } catch (_) {
        fail();
      }
    }
  }

  function textFromTarget(btn) {
    // Priority: data-text (explicit, inline strings for suggested fixes)
    // then data-target (CSS selector pointing to an element whose value/text
    // we should copy).
    var inline = btn.getAttribute("data-text");
    if (inline !== null) return inline;
    var selector = btn.getAttribute("data-target");
    if (!selector) return "";
    var el = document.querySelector(selector);
    if (!el) return "";
    if ("value" in el) return el.value;
    return el.textContent || "";
  }

  // ---- Example query library (populated via the chip row) --------------

  var EXAMPLES = {
    valid:
      "-- Valid: specific standard concept, explicit non-zero filter\n" +
      "SELECT person_id, year_of_birth\n" +
      "FROM person\n" +
      "WHERE gender_concept_id = 8532\n" +
      "  AND year_of_birth > 1980;",

    missing_standard:
      "-- Warning: uses concept_ancestor for descendant expansion but\n" +
      "-- never enforces standard_concept = 'S' on the clinical fact field.\n" +
      "WITH risk_concepts AS (\n" +
      "    SELECT descendant_concept_id AS concept_id\n" +
      "    FROM concept_ancestor\n" +
      "    WHERE ancestor_concept_id = 201820\n" +
      ")\n" +
      "SELECT DISTINCT person_id\n" +
      "FROM condition_occurrence\n" +
      "WHERE condition_concept_id IN (SELECT concept_id FROM risk_concepts);",

    unknown_table:
      "-- Error: 'cohort_result' is not an OMOP CDM v5.4 table.\n" +
      "SELECT person_id, cohort_start_date\n" +
      "FROM cohort_result\n" +
      "WHERE cohort_definition_id = 1;",

    multi_statement:
      "-- Two statements — each gets its own result panel\n" +
      "SELECT person_id FROM person WHERE year_of_birth < 1970;\n\n" +
      "SELECT COUNT(*) AS patient_count\n" +
      "FROM condition_occurrence\n" +
      "WHERE condition_concept_id = 201826;",
  };

  function applyExample(key) {
    var sql = EXAMPLES[key];
    if (!sql || !ta) return;
    ta.value = sql;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus();
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-action]");
    if (!btn) return;
    var action = btn.getAttribute("data-action");
    if (action === "copy") {
      copyText(textFromTarget(btn), btn);
    } else if (action === "clear") {
      var selector = btn.getAttribute("data-target");
      var el = selector ? document.querySelector(selector) : null;
      if (el && "value" in el) {
        el.value = "";
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.focus();
      }
    } else if (action === "toggle-view") {
      var panel = btn.closest(".query-result");
      if (!panel) return;
      var current = panel.getAttribute("data-view") || "formatted";
      panel.setAttribute("data-view", current === "json" ? "formatted" : "json");
    } else if (action === "example") {
      applyExample(btn.getAttribute("data-example"));
    }
  });
})();
