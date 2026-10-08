// Sends each photo in its own request: the server prepares and reads one
// page per request (15-35 s), so pages read in parallel and a slow page
// never holds the others. Two at a time keeps the phone's connection light.
(function () {
  "use strict";
  var MAX_PARALLEL = 2;
  var list = document.getElementById("pages");
  if (!list) return;
  var url = list.dataset.uploadUrl;
  var finish = document.querySelector("[data-finish]");
  var csrf = JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"];
  var queue = [];
  var running = 0;

  function busy() { return running > 0 || queue.length > 0; }

  function refreshFinish() {
    if (!finish) return;
    var pages = list.querySelectorAll(".page-card[data-status]").length;
    var pending = list.querySelectorAll(".page-card[data-status='pending']").length;
    finish.disabled = busy() || pages === 0 || pending > 0;
    finish.textContent = busy() ? "Leyendo páginas…" : "Listo, revisar renglones";
  }

  function placeholder(name) {
    var li = document.createElement("li");
    li.className = "page-card";
    li.dataset.status = "pending";
    li.innerHTML = '<div class="page-thumb placeholder"><span class="spinner" aria-hidden="true"></span></div>' +
      '<div class="page-info"><strong></strong><span class="muted small">Leyendo la página… tarda unos segundos</span></div>';
    li.querySelector("strong").textContent = name;
    return li;
  }

  function send(file, target, action) {
    running++;
    refreshFinish();
    var body = new FormData();
    body.append("photo", file, file.name);
    fetch(action, { method: "POST", body: body, headers: { "X-CSRFToken": csrf }, credentials: "same-origin" })
      .then(function (r) { return r.text().then(function (html) { return { ok: r.ok || r.status === 400, html: html }; }); })
      .then(function (res) {
        if (res.ok) { target.outerHTML = res.html; }
        else { target.querySelector(".small").textContent = "No se pudo subir. Revisa tu conexión e inténtalo de nuevo."; target.dataset.status = "failed"; }
      })
      .catch(function () {
        target.dataset.status = "failed";
        target.querySelector(".small").textContent = "Sin conexión. La foto sigue en tu teléfono: vuelve a elegirla.";
      })
      .finally(function () { running--; next(); refreshFinish(); });
  }

  function next() {
    while (running < MAX_PARALLEL && queue.length) {
      var job = queue.shift();
      send(job.file, job.target, job.action);
    }
  }

  function add(files) {
    Array.prototype.forEach.call(files, function (file) {
      var li = placeholder(file.name);
      list.appendChild(li);
      queue.push({ file: file, target: li, action: url });
    });
    next();
    refreshFinish();
  }

  document.addEventListener("change", function (e) {
    var input = e.target;
    if (input.matches("[data-upload]") && input.files.length) {
      add(input.files);
      input.value = "";
    } else if (input.matches("[data-replace]") && input.files.length) {
      var card = input.closest(".page-card");
      card.dataset.status = "pending";
      queue.push({ file: input.files[0], target: card, action: input.dataset.replace });
      next();
    }
  });

  ["dragover", "drop"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      e.preventDefault();
      if (type === "drop" && e.dataTransfer.files.length) add(e.dataTransfer.files);
    });
  });

  window.addEventListener("beforeunload", function (e) {
    if (busy()) { e.preventDefault(); e.returnValue = ""; }
  });

  refreshFinish();
})();
