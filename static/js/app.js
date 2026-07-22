/* Enamad admin panel — local scripts (no external CDN). */

/* Searchable <select>: turns a native select tagged with [data-searchable]
   into a combobox with a type-to-filter dropdown. The native <select> stays
   in the DOM (hidden) so form submission and existing change-listeners keep
   working; we just mirror its value. Respects option.hidden/disabled so the
   dependent province -> city filtering keeps working.

   data-multiple / multiple → Select2-style tags + searchable dropdown. */
(function () {
  "use strict";

  function enhance(select) {
    if (select.dataset.ssReady) return;
    select.dataset.ssReady = "1";

    var multi = select.hasAttribute("multiple") || select.dataset.multiple === "true";
    if (multi) {
      enhanceMulti(select);
      return;
    }
    enhanceSingle(select);
  }

  function enhanceSingle(select) {
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
      if (panel.hidden) {
        panel.hidden = false;
        wrap.classList.add("open");
      }
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
      setTimeout(close, 120);
    });

    select.addEventListener("change", syncInput);
    syncInput();
  }

  function enhanceMulti(select) {
    select.multiple = true;

    var wrap = document.createElement("div");
    wrap.className = "ss ss-multi";
    if (select.dataset.ssClass) wrap.className += " " + select.dataset.ssClass;

    var box = document.createElement("div");
    box.className = "ss-box";
    box.tabIndex = 0;

    var choices = document.createElement("ul");
    choices.className = "ss-choices";

    var searchLi = document.createElement("li");
    searchLi.className = "ss-search";

    var input = document.createElement("input");
    input.type = "text";
    input.className = "ss-search-input";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = select.getAttribute("data-placeholder") || "جستجو…";

    searchLi.appendChild(input);
    choices.appendChild(searchLi);
    box.appendChild(choices);

    var panel = document.createElement("div");
    panel.className = "ss-panel";
    panel.hidden = true;

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(box);
    wrap.appendChild(panel);
    wrap.appendChild(select);
    select.classList.add("ss-native");

    var activeIndex = -1;
    var visibleOptions = [];
    var closing = false;

    function selectedOptions() {
      return Array.prototype.filter.call(select.options, function (opt) {
        return opt.selected && opt.value;
      });
    }

    function findOption(value) {
      return Array.prototype.find.call(select.options, function (opt) {
        return opt.value === value;
      });
    }

    function setSelected(value, on) {
      var opt = findOption(value);
      if (!opt) return;
      opt.selected = !!on;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function syncTags() {
      Array.prototype.slice.call(choices.querySelectorAll(".ss-choice")).forEach(function (el) {
        el.remove();
      });
      selectedOptions().forEach(function (opt) {
        var li = document.createElement("li");
        li.className = "ss-choice";
        li.title = opt.text;

        var label = document.createElement("span");
        label.className = "ss-choice-label";
        label.textContent = opt.text;

        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "ss-choice-remove";
        remove.setAttribute("aria-label", "حذف");
        remove.textContent = "×";
        remove.addEventListener("mousedown", function (e) {
          e.preventDefault();
          e.stopPropagation();
        });
        remove.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          setSelected(opt.value, false);
          syncTags();
          if (!panel.hidden) render(input.value);
          input.focus();
        });

        li.appendChild(label);
        li.appendChild(remove);
        choices.insertBefore(li, searchLi);
      });

      var has = selectedOptions().length > 0;
      input.placeholder = has ? "" : (select.getAttribute("data-placeholder") || "جستجو…");
      wrap.classList.toggle("has-value", has);
    }

    function render(filter) {
      panel.innerHTML = "";
      visibleOptions = [];
      activeIndex = -1;
      var needle = (filter || "").trim().toLowerCase();
      var chosen = {};
      selectedOptions().forEach(function (opt) {
        chosen[opt.value] = true;
      });

      Array.prototype.forEach.call(select.options, function (opt) {
        if (opt.hidden || opt.disabled || !opt.value) return;
        // Select2-style: already-selected items live as tags, hide from list.
        if (chosen[opt.value]) return;
        var label = opt.text;
        if (needle && label.toLowerCase().indexOf(needle) === -1) return;

        var item = document.createElement("div");
        item.className = "ss-option";
        item.textContent = label;
        item.dataset.value = opt.value;
        var idx = visibleOptions.length;
        item.addEventListener("mousedown", function (e) {
          e.preventDefault();
          setSelected(opt.value, true);
          input.value = "";
          syncTags();
          render("");
          input.focus();
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
        empty.textContent = needle ? "موردی یافت نشد" : "همه موارد انتخاب شده‌اند";
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
      if (closing) return;
      render(input.value);
      panel.hidden = false;
      wrap.classList.add("open");
    }

    function close() {
      panel.hidden = true;
      wrap.classList.remove("open");
      input.value = "";
      activeIndex = -1;
    }

    box.addEventListener("mousedown", function (e) {
      if (e.target.closest(".ss-choice-remove")) return;
      e.preventDefault();
      input.focus();
    });

    input.addEventListener("focus", function () {
      open();
    });
    input.addEventListener("input", function () {
      open();
      render(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (panel.hidden) open();
        setActive(Math.min(activeIndex + 1, Math.max(visibleOptions.length - 1, 0)));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        if (!panel.hidden && activeIndex >= 0 && visibleOptions[activeIndex]) {
          e.preventDefault();
          visibleOptions[activeIndex].dispatchEvent(new Event("mousedown"));
        }
      } else if (e.key === "Escape") {
        close();
        input.blur();
      } else if (e.key === "Backspace" && !input.value) {
        var opts = selectedOptions();
        if (opts.length) {
          e.preventDefault();
          setSelected(opts[opts.length - 1].value, false);
          syncTags();
          if (!panel.hidden) render("");
        }
      }
    });
    input.addEventListener("blur", function () {
      closing = true;
      setTimeout(function () {
        close();
        closing = false;
      }, 150);
    });

    select.addEventListener("change", syncTags);
    syncTags();
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
