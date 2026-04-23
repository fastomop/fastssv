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

  function update() {
    if (!hl) return;
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
  function sync() {
    if (!pre) return;
    pre.scrollTop = ta.scrollTop;
    pre.scrollLeft = ta.scrollLeft;
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
    }
  });
})();
