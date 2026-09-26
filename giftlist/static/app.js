// Small progressive enhancements. Everything works without this file.
(function () {
  document.documentElement.classList.add("js");
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";

  // --- Gerry: leaves after 6s, stays while you're poking at him ---------------
  var gerry = document.querySelector("[data-gerry]");
  if (gerry) {
    var timer;
    var leave = function () {
      gerry.classList.add("gone");
      setTimeout(function () { gerry.remove(); }, 400);
    };
    var arm = function () { clearTimeout(timer); timer = setTimeout(leave, 6000); };
    var hold = function () { clearTimeout(timer); };
    gerry.addEventListener("pointerenter", hold);
    gerry.addEventListener("focusin", hold);
    gerry.addEventListener("pointerleave", arm);
    gerry.querySelector("[data-close]").addEventListener("click", leave);
    arm();
  }

  // --- drag to reorder (SortableJS if it loaded; arrow buttons otherwise) ----
  var list = document.querySelector("[data-reorder]");
  if (list && window.Sortable) {
    window.Sortable.create(list, {
      handle: ".handle",
      animation: 150,
      onEnd: function () {
        var ids = Array.prototype.map.call(list.querySelectorAll("[data-id]"), function (li) {
          return parseInt(li.dataset.id, 10);
        });
        fetch(list.dataset.reorder, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify({ ids: ids }),
        }).then(function (r) { if (!r.ok) location.reload(); });
      },
    });
  }

  // --- link preview on the add/edit form ------------------------------------
  var form = document.querySelector("[data-item-form]");
  if (form) {
    var url = form.querySelector("[name=url]");
    var title = form.querySelector("[name=title]");
    var price = form.querySelector("[name=price]");
    var image = form.querySelector("[name=image]");
    var photo = form.querySelector("[data-photo]");
    var status = form.querySelector("[data-status]");
    var priceHint = form.querySelector("[data-price-hint]");
    var last = "";
    var say = function (text, bad) {
      status.textContent = text;
      status.classList.toggle("warn", !!bad);
    };
    var look = function () {
      var value = url.value.trim();
      if (!value || value === last) return;
      last = value;
      say("Having a look...");
      fetch("/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ url: value }),
      })
        .then(function (r) { return r.json(); })
        .then(function (p) {
          if (!p.title && !p.image) {
            say("That shop wouldn't talk to us. Typical. Fill it in yourself.", true);
            return;
          }
          if (p.title && !title.value) title.value = p.title;
          if (p.image) {
            image.value = p.image;
            photo.src = p.image_url;
            photo.hidden = false;
          }
          if (p.price && !price.value) price.value = p.price;
          priceHint.hidden = !!price.value;
          say("Got it. Check we didn't make a hames of it.");
        })
        .catch(function () { say("That shop wouldn't talk to us. Typical. Fill it in yourself.", true); });
    };
    url.addEventListener("change", look);
    url.addEventListener("paste", function () { setTimeout(look, 50); });
  }

  // --- copy buttons ------------------------------------------------------------
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var input = document.getElementById(b.dataset.copy);
      if (navigator.clipboard) {
        navigator.clipboard.writeText(input.value).then(function () { b.textContent = "Copied"; });
      } else {
        input.select();
      }
    });
  });

  // --- confirm destructive buttons ---------------------------------------------
  document.querySelectorAll("[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    });
  });

  // --- checkboxes that submit their form the moment you tick them --------------
  document.querySelectorAll("[data-auto-submit]").forEach(function (cb) {
    cb.addEventListener("change", function () { cb.form.submit(); });
  });
})();
