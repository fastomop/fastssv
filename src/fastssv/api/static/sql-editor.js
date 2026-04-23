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

  // Hard cap on editor height: anything past the 999th line is dropped
  // silently in the same spirit as textarea's built-in maxlength. The file
  // importer does its own pre-flight check and surfaces an explicit alert.
  var MAX_LINES = 999;

  function enforceLineLimit() {
    var v = ta.value || "";
    var newlines = 0;
    for (var i = 0; i < v.length; i++) {
      if (v.charCodeAt(i) === 10) newlines++;
    }
    if (newlines + 1 <= MAX_LINES) return false;
    // Keep the selection stable when trimming content the user didn't type
    // themselves (e.g. a large paste).
    var lines = v.split("\n");
    ta.value = lines.slice(0, MAX_LINES).join("\n");
    return true;
  }

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
    enforceLineLimit();
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
      var panel = btn.closest(".query-block, .query-result");
      if (!panel) return;
      var current = panel.getAttribute("data-view") || "formatted";
      panel.setAttribute("data-view", current === "json" ? "formatted" : "json");
    } else if (action === "example") {
      applyExample(btn.getAttribute("data-example"));
    } else if (action === "import") {
      var inputSelector = btn.getAttribute("data-target");
      var input = inputSelector ? document.querySelector(inputSelector) : null;
      if (input) input.click();
    } else if (action === "download") {
      var text = textFromTarget(btn);
      if (!text) return;
      var filename = btn.getAttribute("data-filename") || "fastssv-report.json";
      try {
        var blob = new Blob([text], { type: "application/json;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        flash(btn, '<span>Saved</span>');
      } catch (err) {
        console.error("Download failed:", err);
        flash(btn, '<span>Failed</span>');
      }
    }
  });

  // ---- File import: read one or more .sql files into the editor --------

  var fileInput = document.getElementById("sql-file-input");

  function setImporting(on) {
    var btns = document.querySelectorAll('[data-action="import"]');
    for (var i = 0; i < btns.length; i++) {
      if (on) {
        btns[i].setAttribute("data-importing", "1");
        btns[i].disabled = true;
      } else {
        btns[i].removeAttribute("data-importing");
        btns[i].disabled = false;
      }
    }
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      var files = Array.prototype.slice.call(fileInput.files || []);
      if (files.length === 0) return;

      // Hard client-side size cap: match the server's max_sql_bytes so we
      // never ship a payload the server would reject AND never set a
      // megabyte-scale string on the textarea (which would freeze Prism and
      // the browser tab). The textarea advertises the limit via maxlength.
      var maxBytes = parseInt(ta.getAttribute("maxlength") || "100000", 10);
      var totalBytes = files.reduce(function (s, f) { return s + (f.size || 0); }, 0);
      var existingBytes = new Blob([ta.value || ""]).size;

      if (existingBytes + totalBytes > maxBytes) {
        window.alert(
          "Import too large — FastSSV caps editor content at " + formatBytes(maxBytes) + ".\n\n" +
          "Selected files:    " + formatBytes(totalBytes) + "\n" +
          "Editor already has: " + formatBytes(existingBytes) + "\n" +
          "Combined total:    " + formatBytes(existingBytes + totalBytes) + "\n\n" +
          "Clear the editor first, or import a smaller file. For bulk batch " +
          "validation, run the CLI against your files directly:\n" +
          "  fastssv path/to/queries.sql"
        );
        fileInput.value = "";
        return;
      }

      setImporting(true);

      var readers = files.map(function (file) {
        return new Promise(function (resolve, reject) {
          var reader = new FileReader();
          reader.onload = function (e) {
            resolve({ name: file.name, text: String(e.target.result || "") });
          };
          reader.onerror = function () {
            reject(reader.error || new Error("read failed"));
          };
          reader.readAsText(file);
        });
      });

      Promise.all(readers).then(function (results) {
        var parts = results.map(function (r) {
          var trimmed = (r.text || "").replace(/\s+$/, "");
          if (!trimmed) return "";
          return "-- File: " + r.name + "\n" + trimmed;
        }).filter(Boolean);

        var combined = parts.join("\n\n");
        if (!combined) {
          window.alert("The selected file" + (files.length > 1 ? "s were" : " was") + " empty.");
          return;
        }

        var existing = ta.value ? ta.value.replace(/\s+$/, "") : "";
        var draft = existing ? existing + "\n\n" + combined : combined;
        var draftLines = draft.split("\n").length;
        if (draftLines > MAX_LINES) {
          window.alert(
            "Import too long — FastSSV caps the editor at " + MAX_LINES + " lines.\n\n" +
            "Selected content would produce " + draftLines + " lines.\n\n" +
            "For larger batches, run the CLI against your files directly:\n" +
            "  fastssv path/to/queries.sql"
          );
          return;
        }
        ta.value = draft;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
        ta.focus();
      }).catch(function (err) {
        console.error("SQL file import failed:", err);
        window.alert(
          "Failed to read one or more files.\n\n" +
          (err && err.message ? err.message : "See browser console for details.")
        );
      }).then(function () {
        setImporting(false);
        // Reset so the same file(s) can be re-imported on next pick.
        fileInput.value = "";
      });
    });
  }

  // ---- Smooth scroll to results after HTMX swap -------------------------

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function smoothScrollTo(targetY, duration) {
    var startY = window.scrollY || window.pageYOffset;
    var distance = targetY - startY;
    if (Math.abs(distance) < 2) return;
    var startTime = performance.now();
    function step(now) {
      var elapsed = now - startTime;
      var progress = Math.min(elapsed / duration, 1);
      window.scrollTo(0, startY + distance * easeInOutCubic(progress));
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var target = evt.target;
    if (!target || target.id !== "results") return;
    // Run Prism over the freshly-swapped fragment so any .language-json /
    // .language-sql blocks get coloured. highlightAllUnder is a no-op when
    // Prism isn't loaded yet; the `defer` order in index.html ensures it is.
    if (window.Prism && Prism.highlightAllUnder) {
      Prism.highlightAllUnder(target);
    }
    // Wait one frame so the browser has computed the final layout of the
    // just-swapped content, then ease the window to show the results panel
    // near the top of the viewport with a bit of breathing room above.
    requestAnimationFrame(function () {
      var rect = target.getBoundingClientRect();
      var currentY = window.scrollY || window.pageYOffset;
      var targetY = rect.top + currentY - 24;  // 24px offset from the top
      smoothScrollTo(targetY, 700);
    });
  });
})();
