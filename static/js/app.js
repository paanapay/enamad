/* Enamad admin panel — local scripts (no external CDN). */

/* Searchable <select>: turns a native select tagged with [data-searchable]
   into a combobox with a type-to-filter dropdown. The native <select> stays
   in the DOM (hidden) so form submission and existing change-listeners keep
   working; we just mirror its value. Respects option.hidden/disabled so the
   dependent province -> city filtering keeps working. */
(function () {
  "use strict";

  function enhance(select) {
    if (select.dataset.ssReady) return;
    select.dataset.ssReady = "1";

    var wrap = document.createElement("div");
    wrap.className = "ss";
    if (select.dataset.ssClass) wrap.className += " " + select.dataset.ssClass;

    var input = document.createElement("input");
    input.type = "text";
    input.className = "ss-input";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = select.getAttribute("data-placeholder") || "جستجو…";

    var panel = document.createElement("div");
    panel.className = "ss-panel";
    panel.hidden = true;

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(input);
    wrap.appendChild(panel);
    wrap.appendChild(select);
    select.classList.add("ss-native");

    var activeIndex = -1;
    var visibleOptions = [];

    function selectedLabel() {
      var opt = select.options[select.selectedIndex];
      return opt ? opt.text : "";
    }

    function syncInput() {
      input.value = selectedLabel();
    }

    function render(filter) {
      panel.innerHTML = "";
      visibleOptions = [];
      activeIndex = -1;
      var needle = (filter || "").trim().toLowerCase();
      Array.prototype.forEach.call(select.options, function (opt) {
        if (opt.hidden || opt.disabled) return;
        var label = opt.text;
        if (needle && label.toLowerCase().indexOf(needle) === -1) return;
        var item = document.createElement("div");
        item.className = "ss-option";
        if (opt.value === select.value) item.classList.add("selected");
        item.textContent = label;
        item.dataset.value = opt.value;
        var idx = visibleOptions.length;
        item.addEventListener("mousedown", function (e) {
          e.preventDefault();
          commit(opt.value);
        });
        item.addEventListener("mousemove", function () {
          setActive(idx);
        });
        panel.appendChild(item);
        visibleOptions.push(item);
      });
      if (!visibleOptions.length) {
        var empty = document.createElement("div");
        empty.className = "ss-empty";
        empty.textContent = "موردی یافت نشد";
        panel.appendChild(empty);
      }
    }

    function setActive(idx) {
      if (activeIndex >= 0 && visibleOptions[activeIndex]) {
        visibleOptions[activeIndex].classList.remove("active");
      }
      activeIndex = idx;
      var el = visibleOptions[activeIndex];
      if (el) {
        el.classList.add("active");
        el.scrollIntoView({ block: "nearest" });
      }
    }

    function open() {
      if (!panel.hidden) return;
      render("");
      panel.hidden = false;
      wrap.classList.add("open");
    }

    function close() {
      panel.hidden = true;
      wrap.classList.remove("open");
      syncInput();
    }

    function commit(value) {
      if (select.value !== value) {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      syncInput();
      close();
    }

    input.addEventListener("focus", function () {
      open();
      input.select();
    });
    input.addEventListener("input", function () {
      if (panel.hidden) panel.hidden = false;
      wrap.classList.add("open");
      render(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (panel.hidden) open();
        setActive(Math.min(activeIndex + 1, visibleOptions.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        if (!panel.hidden && activeIndex >= 0 && visibleOptions[activeIndex]) {
          e.preventDefault();
          commit(visibleOptions[activeIndex].dataset.value);
        }
      } else if (e.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", function () {
      // Delay so a click/mousedown on an option runs first.
      setTimeout(close, 120);
    });

    // Keep the input label in sync when the native value changes elsewhere
    // (e.g. the dependent city filter resetting the selection).
    select.addEventListener("change", syncInput);

    syncInput();
  }

  function init() {
    var selects = document.querySelectorAll("select[data-searchable]");
    Array.prototype.forEach.call(selects, enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
